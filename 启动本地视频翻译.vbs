Set shell = CreateObject("WScript.Shell")
Set fs = CreateObject("Scripting.FileSystemObject")
project = fs.GetParentFolderName(WScript.ScriptFullName)
python = project & "\.venv\Scripts\python.exe"
If Not fs.FileExists(python) Then
  MsgBox "Create the project Python environment first. See README.md.", 48, "Local Video Translator"
  WScript.Quit 1
End If
If Not fs.FileExists(project & "\config.json") Then
  MsgBox "Copy config.example.json to config.json and set your model paths. See README.md.", 48, "Local Video Translator"
  WScript.Quit 1
End If
shell.CurrentDirectory = project
shell.Run Chr(34) & python & Chr(34) & " " & Chr(34) & project & "\run.py" & Chr(34), 0, False
