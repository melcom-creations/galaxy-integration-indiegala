param(
    [Parameter(Mandatory = $false)]
    [string]$CurrentPath = ""
)

$ErrorActionPreference = "Stop"

Add-Type -AssemblyName System.Drawing
Add-Type -AssemblyName System.Windows.Forms

function Resolve-IndieGalaFolder {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Value
    )

    $expanded = [Environment]::ExpandEnvironmentVariables($Value.Trim().Trim('"')).TrimEnd('\', '/')
    if ([string]::IsNullOrWhiteSpace($expanded)) {
        throw "Enter the full path to your IndieGala folder."
    }
    if (-not [IO.Path]::IsPathRooted($expanded)) {
        throw "The IndieGala folder path must be absolute."
    }

    $normalized = [IO.Path]::GetFullPath($expanded)
    $folderName = [IO.Path]::GetFileName($normalized.TrimEnd('\', '/'))
    if (-not $folderName.Equals("IndieGala", [StringComparison]::OrdinalIgnoreCase)) {
        throw 'The selected folder must be named "IndieGala".'
    }

    [IO.Directory]::CreateDirectory($normalized) | Out-Null
    return $normalized.TrimEnd('\', '/')
}

[Windows.Forms.Application]::EnableVisualStyles()

$form = New-Object Windows.Forms.Form
$form.Text = "Change IndieGala Games Folder"
$form.StartPosition = "CenterScreen"
$form.ClientSize = New-Object Drawing.Size(600, 205)
$form.FormBorderStyle = "FixedDialog"
$form.MaximizeBox = $false
$form.MinimizeBox = $false
$form.ShowIcon = $false
$form.TopMost = $true

$title = New-Object Windows.Forms.Label
$title.AutoSize = $true
$title.Font = New-Object Drawing.Font("Segoe UI", 12, [Drawing.FontStyle]::Bold)
$title.Location = New-Object Drawing.Point(18, 16)
$title.Text = "IndieGala games folder"
$form.Controls.Add($title)

$description = New-Object Windows.Forms.Label
$description.AutoSize = $true
$description.Location = New-Object Drawing.Point(20, 48)
$description.Text = "Change the folder where your installed IndieGala games are stored."
$form.Controls.Add($description)

$pathBox = New-Object Windows.Forms.TextBox
$pathBox.Location = New-Object Drawing.Point(22, 78)
$pathBox.Size = New-Object Drawing.Size(474, 25)
$pathBox.Text = $CurrentPath
$form.Controls.Add($pathBox)

$browseButton = New-Object Windows.Forms.Button
$browseButton.Location = New-Object Drawing.Point(506, 76)
$browseButton.Size = New-Object Drawing.Size(72, 28)
$browseButton.Text = "Browse..."
$browseButton.Add_Click({
    $picker = New-Object Windows.Forms.FolderBrowserDialog
    $picker.Description = "Select the IndieGala games folder"
    $picker.ShowNewFolderButton = $true
    if (Test-Path -LiteralPath $pathBox.Text -PathType Container) {
        $picker.SelectedPath = $pathBox.Text
    }
    if ($picker.ShowDialog($form) -eq [Windows.Forms.DialogResult]::OK) {
        $pathBox.Text = $picker.SelectedPath
    }
    $picker.Dispose()
})
$form.Controls.Add($browseButton)

$hint = New-Object Windows.Forms.Label
$hint.AutoSize = $true
$hint.ForeColor = [Drawing.Color]::DimGray
$hint.Location = New-Object Drawing.Point(20, 112)
$hint.Text = 'The final folder must be named "IndieGala", for example C:\Games\IndieGala.'
$form.Controls.Add($hint)

$cancelButton = New-Object Windows.Forms.Button
$cancelButton.Location = New-Object Drawing.Point(406, 158)
$cancelButton.Size = New-Object Drawing.Size(82, 30)
$cancelButton.Text = "Cancel"
$cancelButton.DialogResult = [Windows.Forms.DialogResult]::Cancel
$form.Controls.Add($cancelButton)
$form.CancelButton = $cancelButton

$saveButton = New-Object Windows.Forms.Button
$saveButton.Location = New-Object Drawing.Point(496, 158)
$saveButton.Size = New-Object Drawing.Size(82, 30)
$saveButton.Text = "Save"
$saveButton.Add_Click({
    try {
        $script:SelectedPath = Resolve-IndieGalaFolder -Value $pathBox.Text
        $form.DialogResult = [Windows.Forms.DialogResult]::OK
        $form.Close()
    }
    catch {
        [Windows.Forms.MessageBox]::Show(
            $form,
            $_.Exception.Message,
            "Invalid IndieGala Folder",
            [Windows.Forms.MessageBoxButtons]::OK,
            [Windows.Forms.MessageBoxIcon]::Warning
        ) | Out-Null
        $pathBox.Focus()
        $pathBox.SelectAll()
    }
})
$form.Controls.Add($saveButton)
$form.AcceptButton = $saveButton

$form.Add_Shown({
    $pathBox.Focus()
    $pathBox.SelectionStart = $pathBox.Text.Length
})

$result = $form.ShowDialog()
$form.Dispose()

if ($result -eq [Windows.Forms.DialogResult]::OK -and $script:SelectedPath) {
    $bytes = [Text.Encoding]::UTF8.GetBytes($script:SelectedPath)
    [Console]::Out.WriteLine([Convert]::ToBase64String($bytes))
    exit 0
}

exit 2
