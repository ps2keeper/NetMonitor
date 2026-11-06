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

' 1. Start monitor in background (completely hidden without console window)
ws.Run pythonwExe & " """ & currentDir & "\monitor.py""", 0, False

' 2. Start mini desktop floating widget
ws.Run pythonwExe & " """ & currentDir & "\mini_widget.py""", 0, False

' 3. Open the dashboard once the server is ready (port comes from WEB_PORT / nm_config)
ws.Run pythonwExe & " """ & currentDir & "\monitor.py"" --open", 0, False