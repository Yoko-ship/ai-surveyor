$E = "C:\Program Files (x86)\Microsoft\Edge\Application\msedge.exe"
$SP = "C:/Users/Max/AppData/Local/Temp/claude/C--Users-Max/d4f68bc8-abb2-4574-a96f-cab1f6c71ac5/scratchpad"
$OUT = "C:\Users\Max\ai-surveyor\sandbox"
$P = "file:///$SP"
$list = @(
 @("ds3_act.png", "$P/surveyor_prototype_ds.html%23act-light", 2400),@("ds3_sec4.png", "$P/surveyor_prototype_ds.html%23act-s4-light", 5200)
)
$i = 0
foreach ($s in $list) {
  $i++
  $ud = "$env:TEMP\edge_ds_$i_$(Get-Random)"
  $args = @("--headless=new","--disable-gpu","--hide-scrollbars","--no-first-run","--allow-file-access-from-files","--user-data-dir=$ud","--window-size=500,$($s[2])","--virtual-time-budget=5000","--screenshot=$OUT\$($s[0])","$P/ds_build/frame.html?$($s[1])&$($s[2])")
  Start-Process -FilePath $E -ArgumentList $args -Wait -NoNewWindow
  "$($s[0]) " + (Test-Path "$OUT\$($s[0])")
}
