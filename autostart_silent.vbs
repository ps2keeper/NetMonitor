' NetMonitor 开机静默启动服务（完全隐藏控制台黑框，不主动弹出浏览器干扰开机）
Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)

' 启动后台监控程序（窗口参数 0 表示完全隐藏窗口，第三参数 False 表示异步不阻塞）
ws.Run "pythonw """ & currentDir & "\monitor.py""", 0, False
