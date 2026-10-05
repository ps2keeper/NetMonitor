Option Explicit
Dim ws, fso, currentDir

Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = currentDir

' Launch monitor service silently at Windows startup
ws.Run "cmd.exe /c pythonw """ & currentDir & "\monitor.py""", 0, False