# 작업 중 PC가 대기·절전으로 들어가지 않게 붙잡아 둔다(Windows, 관리자 권한 불필요).
# - 이 창(프로세스)이 살아 있는 동안만 유지된다. 닫거나 재부팅하면 원래대로.
# - Windows 설정은 바꾸지 않는다. 화면은 꺼져도 되고 시스템만 깨어 있게 한다.
# - 덮개를 닫아도 계속 하려면 충전기를 꽂아 둔다(이 PC는 배터리일 때 덮개를 닫으면 절전 설정).
# 사용: powershell -NoProfile -WindowStyle Hidden -File scripts\keep-awake.ps1 [-Hours 12]
param([double]$Hours = 12)
Add-Type -Namespace Win32 -Name Power -MemberDefinition @'
[System.Runtime.InteropServices.DllImport("kernel32.dll", SetLastError = true)]
public static extern uint SetThreadExecutionState(uint esFlags);
'@
$ES_CONTINUOUS = [uint32]'0x80000000'
$ES_SYSTEM_REQUIRED = [uint32]'0x00000001'
$ES_AWAYMODE_REQUIRED = [uint32]'0x00000040'
$until = (Get-Date).AddHours($Hours)
try {
    while ((Get-Date) -lt $until) {
        [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS -bor $ES_SYSTEM_REQUIRED -bor $ES_AWAYMODE_REQUIRED)
        Start-Sleep -Seconds 60
    }
} finally {
    [void][Win32.Power]::SetThreadExecutionState($ES_CONTINUOUS)
}
