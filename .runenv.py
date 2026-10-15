import os, subprocess
for k in ("NAVER_CLIENT_ID","NAVER_CLIENT_SECRET"):
    try:
        v=subprocess.check_output(["reg","query","HKCU\Environment","/v",k],text=True)
        os.environ[k]=v.split()[-1]
    except Exception: pass
print("NAVER_CLIENT_ID:", bool(os.environ.get("NAVER_CLIENT_ID")))
