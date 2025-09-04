##vm 자동 발급해주는 함수

import os
import base64
import uuid
from azure.identity import DefaultAzureCredential
from azure.mgmt.compute import ComputeManagementClient
from azure.mgmt.network import NetworkManagementClient
from azure.mgmt.resource import ResourceManagementClient

# --- Configuration from Environment Variables ---
# These values are now set by the cloud-init script during VM startup.
credential = DefaultAzureCredential()
SUBSCRIPTION_ID = os.getenv("AZURE_SUBSCRIPTION_ID")
RESOURCE_GROUP = os.getenv("AZURE_RESOURCE_GROUP")
LOCATION = os.getenv("AZURE_LOCATION", "koreacentral") # Default value as fallback
VM_SIZE = os.getenv("VM_SIZE", "Standard_B1s")
ADMIN_USERNAME = os.getenv("ADMIN_USERNAME")
SSH_PUBLIC_KEY_PATH = os.getenv("SSH_PUBLIC_KEY_PATH")
PROXY_IP = os.getenv("PROXY_IP") # This will be set by cloud-init from Terraform output

# Simple check for required environment variables
if not all([SUBSCRIPTION_ID, RESOURCE_GROUP, ADMIN_USERNAME, SSH_PUBLIC_KEY_PATH, PROXY_IP]):
    raise ValueError("One or more required environment variables are not set.")


def launch_user_vm():
    session_id = str(uuid.uuid4())[:8]

    # === Read and render cloud-init template ===
    with open("templates/user-vm-cloud-init.tpl.sh", "r") as f:
        template = f.read()

    rendered_cloud_init = template.replace("{{SESSION_ID}}", session_id).replace("{{PROXY_IP}}", PROXY_IP)
    encoded_cloud_init = base64.b64encode(rendered_cloud_init.encode("utf-8")).decode("utf-8")

    compute_client = ComputeManagementClient(credential, SUBSCRIPTION_ID)
    network_client = NetworkManagementClient(credential, SUBSCRIPTION_ID)
    resource_client = ResourceManagementClient(credential, SUBSCRIPTION_ID)

    # === Networking ===
    NIC_NAME = f"nic-{session_id}"
    IP_NAME = f"ip-{session_id}"

    subnet = network_client.subnets.get(RESOURCE_GROUP, "lrn-k8s-main-vnet", "user-subnet")
    nsg = network_client.network_security_groups.get(RESOURCE_GROUP, "main-nsg")

    NSG_NAME = f"nsg-{session_id}"
    nsg_params = {
        "location": LOCATION,
        "security_rules": [{
            "name": "AllowProxyWebSocket",
            "protocol": "Tcp",
            "direction": "Inbound",
            "access": "Allow",
            "priority": 1001,
            "source_address_prefix": PROXY_IP,
            "destination_address_prefix": "*",
            "source_port_range": "*",
            "destination_port_range": "8889"
        }]
    }
    user_nsg = network_client.network_security_groups.begin_create_or_update(
        RESOURCE_GROUP, NSG_NAME, nsg_params).result()

    # NAT Gateway handles outbound connectivity, no public IP assigned

    nic_params = {
        "location": LOCATION,
        "ip_configurations": [{
            "name": "ipconfig1",
            # "subnet": {"id": subnet.id}
            "subnet": {"id": subnet.id}
            
        }],
        "network_security_group": {"id": user_nsg.id}
    }
    nic = network_client.network_interfaces.begin_create_or_update(
        RESOURCE_GROUP, NIC_NAME, nic_params).result()

    # === Launch VM ===
    VM_NAME = f"uservm-{session_id}"
    with open(SSH_PUBLIC_KEY_PATH, "r") as pubkey_file:
        ssh_key = pubkey_file.read()

    vm_params = {
        "location": LOCATION,
        "tags": {
            "session_id": session_id,
            "created_by": "infra-launcher"
        },
        "hardware_profile": {"vm_size": VM_SIZE},
        "os_profile": {
            "computer_name": VM_NAME,
            "admin_username": ADMIN_USERNAME,
            "linux_configuration": {
                "disable_password_authentication": True,
                "ssh": {
                    "public_keys": [{
                        "path": f"/home/{ADMIN_USERNAME}/.ssh/authorized_keys",
                        "key_data": ssh_key
                    }]
                }
            },
            "custom_data": encoded_cloud_init
        },
        "storage_profile": {
            "image_reference": {
                "publisher": "Canonical",
                "offer": "0001-com-ubuntu-server-jammy",
                "sku": "22_04-lts",
                "version": "latest"
            },
            "os_disk": {
                "name": f"{VM_NAME}-osdisk",
                "caching": "ReadWrite",
                "create_option": "FromImage",
                "managed_disk": {
                    "storage_account_type": "Standard_LRS"
                }
            }
        },
        "network_profile": {
            "network_interfaces": [{
                "id": nic.id,
                "primary": True
            }]
        }
    }

    print(f"🚀 Launching VM for session: {session_id}")
    compute_client.virtual_machines.begin_create_or_update(RESOURCE_GROUP, VM_NAME, vm_params).result()
    print("✅ VM launched and cloud-init applied.")
    print("🔒 Note: VM has no public IP. Admin access should use Bastion host or private proxy.")

    private_ip = nic.ip_configurations[0].private_ip_address
    print(f"🎯 VM IP: {private_ip}")
    return session_id, private_ip


def delete_user_vm(session_id):
    print(f"🗑️ Deleting resources for session: {session_id}")
    
    compute_client = ComputeManagementClient(credential, SUBSCRIPTION_ID)
    network_client = NetworkManagementClient(credential, SUBSCRIPTION_ID)
    
    VM_NAME = f"uservm-{session_id}"
    NIC_NAME = f"nic-{session_id}"
    DISK_NAME = f"{VM_NAME}-osdisk"

    try:
        # Azure는 리소스를 동시에 삭제할 수 있으나, 순차적으로 진행하여 로그를 명확히 합니다.
        print(f"   - Deleting Virtual Machine: {VM_NAME}...")
        delete_vm_poller = compute_client.virtual_machines.begin_delete(RESOURCE_GROUP, VM_NAME)
        delete_vm_poller.wait()
        print(f"   - VM {VM_NAME} deleted.")

        print(f"   - Deleting Network Interface: {NIC_NAME}...")
        delete_nic_poller = network_client.network_interfaces.begin_delete(RESOURCE_GROUP, NIC_NAME)
        delete_nic_poller.wait()
        print(f"   - NIC {NIC_NAME} deleted.")

        print(f"   - Deleting OS Disk: {DISK_NAME}...")
        delete_disk_poller = compute_client.disks.begin_delete(RESOURCE_GROUP, DISK_NAME)
        delete_disk_poller.wait()
        print(f"   - Disk {DISK_NAME} deleted.")
        
        print(f"✅ All resources for session {session_id} have been deleted.")
        
    except Exception as e:
        print(f"❌ Error deleting resources for session {session_id}: {e}")
        # API 호출자에게 에러를 전파하여 실패했음을 알립니다.
        raise e


if __name__ == "__main__":
    sid, ip = launch_user_vm()
    print(f"Session ID: {sid}")
    print(f"Public IP: {ip}")
