' Lanzador invisible para las tareas del watchdog EET.
' Arranca PowerShell con la ventana oculta desde el inicio, para que Windows
' Terminal no muestre el flash de consola cada vez que corre la tarea.
'
' Uso: wscript.exe //B //Nologo run-hidden.vbs keep-local
'      wscript.exe //B //Nologo run-hidden.vbs keep-wsl-alive
Option Explicit

Dim sh, fso, dir, script, mode, cmd, rc
Set sh = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

If WScript.Arguments.Count = 0 Then
    WScript.Quit 1
End If

dir = fso.GetParentFolderName(WScript.ScriptFullName)
mode = LCase(WScript.Arguments(0))

If mode = "keep-wsl-alive" Then
    script = dir & "\keep-wsl-alive.ps1"
Else
    script = dir & "\keep-local.ps1"
End If

cmd = "powershell.exe -NoProfile -ExecutionPolicy Bypass -File """ & script & """"
rc = sh.Run(cmd, 0, True)
WScript.Quit rc
