"""
HomeBot 网页控制端启动入口

使用方法:
    cd software/src
    python -m applications.remote_control
    
    # 或指定参数
    python -m applications.remote_control --host 0.0.0.0 --port 5000

然后在手机/平板/电脑浏览器访问:
    http://<机器人IP>:5000
"""
from applications.remote_control.web_server import main

if __name__ == '__main__':
    main()
