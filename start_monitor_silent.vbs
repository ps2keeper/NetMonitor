' NetMonitor 静默启动器（完全无控制台黑框）
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)

' 打开 Web 仪表盘
ws.Run "http://127.0.0.1:8899", 1

' 启动后台监控程序（窗口参数 0 表示完全隐藏无黑框）
ws.Run "pythonw """ & currentDir & "\monitor.py""", 0
