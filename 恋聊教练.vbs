Option Explicit
Dim shell, fs, project, script, launcher, result
Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
project = fs.GetParentFolderName(WScript.ScriptFullName)
script = fs.BuildPath(project, "scripts\bootstrap_desktop.py")
launcher = fs.BuildPath(project, ".venv\Scripts\pythonw.exe")
On Error Resume Next
If fs.FileExists(launcher) Then
  result = shell.Run("""" & launcher & """ """ & script & """", 0, False)
Else
  result = shell.Run("pythonw.exe """ & script & """", 0, False)
  If Err.Number <> 0 Then
    Err.Clear
    result = shell.Run("pyw.exe """ & script & """", 0, False)
  End If
End If
If Err.Number <> 0 Then
  MsgBox "Please install Python 3.11+ with Tcl/Tk and Add Python to PATH, then double-click this file again.", 48, "LoveChat Coach"
  shell.Run "https://www.python.org/downloads/windows/", 1, False
End If
