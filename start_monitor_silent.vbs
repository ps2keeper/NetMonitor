Option Explicit
Dim ws, fso, currentDir

Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = currentDir

' 1. Start monitor in background (completely hidden without console window)
ws.Run "cmd.exe /c pythonw """ & currentDir & "\monitor.py""", 0, False

' 2. Wait briefly for server to bind port
WScript.Sleep 500

' 3. Open dashboard in default browser
ws.Run "http://127.0.0.1:8899", 1