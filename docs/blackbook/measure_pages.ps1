param([string]$Docx = "D:\Projects\GitaGuide\gitaguide\docs\blackbook\Blackbook_GitaGuide.docx",
      [string]$Out = "D:\Projects\GitaGuide\gitaguide\docs\blackbook\_pages.json",
      [string]$Pdf = "D:\Projects\GitaGuide\gitaguide\docs\blackbook\Blackbook_GitaGuide.pdf")
Get-Process WINWORD -ErrorAction SilentlyContinue | Stop-Process -Force
$word = New-Object -ComObject Word.Application
$word.Visible = $false
$doc = $word.Documents.Open($Docx)
$doc.Repaginate()
$res = [ordered]@{}
foreach ($b in $doc.Bookmarks) {
  if ($b.Name -like "bm_*" -or $b.Name -like "f_*") {
    $res[$b.Name] = [int]$b.Range.Information(1)
  }
}
$res["_total"] = [int]$doc.ComputeStatistics(2)
$res | ConvertTo-Json | Set-Content -Encoding UTF8 $Out
$doc.SaveAs([ref]$Pdf, [ref]17)
$doc.Close([ref]0)
$word.Quit()
Write-Output ("pages=" + $res["_total"])
