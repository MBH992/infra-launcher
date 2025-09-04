## launch_user_vm() 호출, 프록시 서버에 등록 요청, API 응답 반환

from fastapi import FastAPI
from fastapi.responses import JSONResponse
from launch_vm import launch_user_vm, delete_user_vm
import requests
import os

PROXY_API = os.getenv("PROXY_API", "http://10.0.1.4:8080/register-session")  # 프록시 서버의 내부 IP로 요청

app = FastAPI()

@app.post("/api/launch-vm")
def launch_vm_endpoint():
    try:
        session_id, private_ip = launch_user_vm()

        # 프록시에 세션 등록
        res = requests.post(PROXY_API, json={
            "sessionId": session_id,
            "vmIp": private_ip
        })

        if res.status_code != 200:
            # 여기서 VM을 다시 삭제해주는 보상 트랜잭션 로직을 추가할 수 있습니다.
            # delete_user_vm(session_id)
            return JSONResponse(
                content={"error": "Failed to register with proxy", "details": res.text},
                status_code=500
            )

        return JSONResponse(
            content={"session_id": session_id, "vm_ip": private_ip},
            status_code=200
        )

    except Exception as e:
        return JSONResponse(
            content={"error": str(e)},
            status_code=500
        )

@app.delete("/api/vm/{session_id}")
def delete_vm_endpoint(session_id: str):
    """
    지정된 session_id에 해당하는 VM과 관련 리소스를 삭제합니다.
    """
    print(f"Received request to delete VM for session: {session_id}")
    try:
        delete_user_vm(session_id)
        return JSONResponse(
            content={"status": "success", "message": f"VM for session {session_id} is being deleted."},
            status_code=200
        )
    except Exception as e:
        # 구체적인 에러는 delete_user_vm 함수에서 이미 로그로 남습니다.
        return JSONResponse(
            content={"error": "Failed to delete VM", "details": str(e)},
            status_code=500
        )
