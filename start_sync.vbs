Set WshShell = CreateObject("WScript.Shell")
Set fso = CreateObject("Scripting.FileSystemObject")

' 获取脚本所在目录
scriptDir = fso.GetParentFolderName(WScript.ScriptFullName)

' 切换到脚本所在目录
WshShell.CurrentDirectory = scriptDir

' 检查配置文件是否存在
configFile = scriptDir & "\config.json"
If Not fso.FileExists(configFile) Then
    ' 静默退出
    WScript.Quit 1
End If

' 检查主程序文件是否存在
mainFile = scriptDir & "\sync_to_nas.py"
If Not fso.FileExists(mainFile) Then
    ' 静默退出
    WScript.Quit 1
End If

' 静默启动 Python 脚本（不显示窗口）
' 优先使用 pythonw（完全静默），如果不可用则使用 python（最小化窗口）
pythonwPath = "pythonw"
pythonPath = "python"

' 检查 pythonw 是否可用
Set checkProc = WshShell.Exec(pythonwPath & " --version")
checkProc.StdOut.Close
checkProc.StdErr.Close
checkProc.Terminate

If checkProc.ExitCode = 0 Then
    ' 使用 pythonw（完全静默，不显示窗口）
    WshShell.Run pythonwPath & " """ & mainFile & """", 0, False
Else
    ' 使用 python（最小化窗口运行）
    WshShell.Run pythonPath & " """ & mainFile & """", 2, False
End If

' 静默退出
WScript.Quit 0
