"""通过本地 API 触发完整预测（trigger-predict），等待结果"""
import json
import urllib.request

URL = "http://localhost:8008/api/admin/trigger-predict"


def main():
    req = urllib.request.Request(URL, method="POST")
    with urllib.request.urlopen(req, timeout=600) as resp:
        body = resp.read().decode("utf-8")
    print(body)


if __name__ == "__main__":
    main()
