Option Explicit
Dim ws, fso, currentDir, pythonwExe, appDataPythonw

Set ws = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")
currentDir = fso.GetParentFolderName(WScript.ScriptFullName)
ws.CurrentDirectory = currentDir

appDataPythonw = ws.ExpandEnvironmentStrings("%LOCALAPPDATA%") & "\Microsoft\WindowsApps\pythonw.exe"
If fso.FileExists(appDataPythonw) Then
    pythonwExe = """" & appDataPythonw & """"
Else
    pythonwExe = "pythonw"
End If

' Launch mini floating widget silently in background without console window flash
ws.Run pythonwExe & " """ & currentDir & "\mini_widget.py""", 0, False
