param([Parameter(Mandatory=$true)][string]$InstalledExe, [switch]$RemoveData)
$ErrorActionPreference = 'Stop'
try {
    $expectedExe = [IO.Path]::GetFullPath($InstalledExe)
    $scheduler = New-Object -ComObject 'Schedule.Service'
    $scheduler.Connect()
    $folder = $scheduler.GetFolder('\')
    foreach ($task in @($folder.GetTasks(1))) {
        $definition = $task.Definition
        if ($definition.RegistrationInfo.Source -ne 'com.porklittlehamburger.wechat-onebot-launcher') { continue }
        if ($definition.Actions.Count -ne 1) { continue }
        $action = $definition.Actions.Item(1)
        if ($action.Type -ne 0 -or -not [string]::Equals([IO.Path]::GetFullPath($action.Path), $expectedExe, [StringComparison]::OrdinalIgnoreCase)) { continue }
        # Never stop a running task/process here; the tray exit completed first.
        $task.Enabled = $false
        $folder.DeleteTask($task.Name, 0)
    }
    if ($RemoveData) {
        $localData = [IO.Path]::GetFullPath([Environment]::GetFolderPath('LocalApplicationData'))
        $dataRoot = [IO.Path]::GetFullPath((Join-Path $localData 'WeChatOneBotLauncher'))
        $expectedRoot = $localData.TrimEnd('\') + '\WeChatOneBotLauncher'
        if (-not [string]::Equals($dataRoot, $expectedRoot, [StringComparison]::OrdinalIgnoreCase)) { throw 'Unexpected data path' }
        if (Test-Path -LiteralPath $dataRoot) {
            $rootItem = Get-Item -LiteralPath $dataRoot -Force
            if ($rootItem.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to delete a redirected data root' }
            # Refuse junctions before recursing so no deletion can escape root.
            $queue = New-Object 'System.Collections.Generic.Queue[string]'
            $queue.Enqueue($dataRoot)
            while ($queue.Count -gt 0) {
                foreach ($item in Get-ChildItem -LiteralPath $queue.Dequeue() -Force) {
                    if ($item.Attributes -band [IO.FileAttributes]::ReparsePoint) { throw 'Refusing to delete data containing links/junctions' }
                    if ($item.PSIsContainer) { $queue.Enqueue($item.FullName) }
                }
            }
            Remove-Item -LiteralPath $dataRoot -Recurse -Force
        }
    }
    exit 0
} catch {
    Write-Output $_.Exception.Message
    exit 1
}
