package deploy

const InstallGuide = `하나님의방패 — 설치 후 사용 안내

이 설치 EXE는 이미 설치된 Windows MT5의 선택한 데이터 폴더에 파일을 배치합니다.
MT5 자체 설치, 브로커 로그인, 주문 실행, EA 자동 부착은 하지 않습니다.

1. 설치 전에 선택할 MT5와 (선택한 경우) NinjaTrader를 정상 종료하세요.
   계좌번호/서버는 MT5에서 직접 확인하세요. 화면의 저장 로그인은 참고값입니다.
2. 터미널마다 설치 안 함 / 마스터 / 슬레이브 / 매매용 EA / 마스터+매매용 EA를 선택합니다.
   동일 스트림에는 마스터 하나, 슬레이브 여러 개를 지정할 수 있습니다.
   한 터미널에 마스터와 슬레이브를 같이 설치하지 않습니다.
   일반 모드와 포터블 모드는 같은 실행 폴더에서 동시 운용할 수 없으므로 중복 선택을 거부합니다.
3. 슬레이브 설치 시 마스터 계좌번호와 서버명을 입력합니다.
   모든 연결 대상은 같은 StreamId를 사용해야 합니다. 기본값은 OZ_MAIN입니다.
   현재 프로젝트의 MMF 연결은 같은 PC/Windows 세션에서 사용합니다. 인터넷 중계 기능을 추가하지 않았습니다.
4. 설치 후 MT5를 실행하고 탐색기 > Expert Advisors > DivineShield에서 파일을 확인합니다.
   보이지 않으면 탐색기를 새로 고침하거나 MT5를 재시작하세요.
5. EA를 차트에 직접 부착하고 입력 탭의 불러오기에서 해당 프리셋을 선택하세요.
   경로: 데이터 폴더\MQL5\Presets\DivineShield\DS5_<발급번호>_<역할>.set
   마스터: DivineShield_Master.ex5, Master 프리셋.
   슬레이브: DivineShield_Slave.ex5, Slave 프리셋.
   매매용: DivineShield_Trading.ex5, Trading 프리셋.
   마스터+매매용은 같은 터미널의 서로 다른 차트에 각각 부착합니다. Part1과 Part2는 독립 실행합니다.
6. 카피시스템은 기존 코드에서 kernel32.dll/ole32.dll을 사용하므로 DLL 가져오기 허용을 확인하세요.
   계좌, 심볼 매핑, 리스크, 포지션 한도, 복구 상태를 확인한 뒤 사용자가 직접 자동매매를 켜세요.
   설치 프로그램은 전역 자동매매/DLL 설정과 계좌 비밀번호를 바꾸지 않습니다.
7. 텔레그램 알림을 사용할 때만 슬레이브와 같은 MT5의 별도 차트에
   DivineShield_Notifier.ex5를 부착하고 Notifier 프리셋을 불러오세요.
   알림의 매직넘버/스트림을 슬레이브와 맞추고 필요한 WebRequest 허용도 직접 확인합니다.
   토큰과 채팅 ID를 설치 EXE나 로그에 수집하지 않습니다.

NinjaTrader (배포 제작 시 포함하고 설치 시 선택한 경우)
- 사용자 데이터 폴더의 bin\Custom\DivineShield.NinjaSlave.dll에 설치됩니다.
- 원래 소스 AddOn이 남아 있으면 중복 로딩을 피하기 위해 설치를 거부합니다. 자동 삭제하지 않습니다.
- NinjaTrader를 재시작한 뒤 New > OZ Multi Account Receiver를 확인하세요.
- 계좌/스트림/마스터 계좌 및 서버/월물/리스크는 기존 AddOn 화면에서 직접 설정해야 합니다.
- NinjaTrader의 실제 참조 DLL과 Roslyn csc.exe로 컴파일은 발급 PC에서 수행합니다. 설치 PC 버전과 호환성을 확인하세요.
- 이 배포 수정본의 Windows/NinjaTrader 실제 로딩 검증 여부는 개발자 검증보고서를 참고하세요.

업데이트 / 백업
- 새 설치 EXE를 발급받아 같은 MT5 폴더와 역할을 선택합니다.
- 다른 EA, 계좌 로그인, 차트 프로필, 거래기록, 전역변수, 기존 OZCopy 복구/리스크 자료는 변경하지 않습니다.
- 설치 도구가 이전에 관리한 파일만 교체/정리합니다. 수정한 이전 .set 파일은 보존합니다.
- 변경된 기존 바이너리나 소유권 없는 같은 이름 파일은 임의 덮어쓰지 않습니다.
- 백업: MT5 데이터\MQL5\Files\DivineShieldDeployment\backups
  Ninja: 사용자 데이터\bin\Custom\DivineShieldDeployment\backups
- 일반 파일 적용 오류는 이미 수행한 변경을 역순으로 복원합니다.
- 정전/프로세스 강제 종료에 대한 완전한 원자성을 보장하지 않습니다.
  transaction-pending.json이 남으면 자동 재설치를 막습니다. 표시된 backup\journal.json을 보고
  existed=true 파일은 before 폴더에서 원위치로 복원하고, existed=false인 새 파일만 제거하세요.
  unrelated files를 삭제하지 마세요. 복원을 확인한 뒤 transaction-pending.json을 제거합니다.

실계좌에 적용하기 전에 데모 환경에서 연결, 만료일, 신규 주문 및 청산/복구 동작을 확인하세요.
`
const LicenseGuide = `하나님의방패 — 배포 라이선스 정책

프로그램 사용 만료일과 설치 EXE의 설치 유효시간은 서로 다릅니다.
- 사용 만료일: 선택한 날짜의 23:59:59까지, 실행 PC의 로컬 달력 날짜 기준입니다.
  그 다음 날 00:00:00부터 새 EA 초기화/새 Ninja 수신기 시작을 거부합니다.
- 설치 유효시간: 발급 완료 시점의 UTC 시각부터 지정한 분 수(1~10080분)입니다.
  EXE를 복사하거나 이름을 바꾸어도 기한은 연장되지 않습니다.
- 이미 정상 초기화된 MT5 EA는 실행 도중 만료되더라도 강제 제거하지 않습니다.
  다음 초기화/재부착/재시작에서 다시 검사합니다. Ninja도 새 수신기/엔진 시작 시 검사합니다.
  이는 모세의지팡이의 시작 시 검사 방식에 맞추고 운용 중 관리 중단을 피하기 위한 정책입니다.
  초 단위 즉시 중단 또는 신규 주문만 선별 차단하는 별도 매매 정책은 추가하지 않았습니다.

발급 시 MT5 복사본의 기존 2026.12.31 CheckExpiry 구현을 배포용 검사로 교체합니다.
원본/수정본 EA 파일은 변경하지 않습니다. OnTick/매매/MMF/리스크/복구 로직을 재작성하지 않습니다.
MT5 전략테스터의 과거 TimeCurrent/TimeLocal 대신 새 임시 파일의 수정시각을 읽고 제거하는
모세 배포 방식으로 실제 PC 로컬 시각을 초기화 시 한 번 검사합니다.

설치 EXE는 RSA-2048/SHA-256 서명으로 라이선스, EXE 본체 해시, 압축 내용 해시와 파일 목록을 검사합니다.
개인키는 개발자 PC 첫 발급 시 만들며 Windows 사용자 DPAPI로 암호화해 .keys에 보관합니다.
개인키/소스/다른 제품 키는 고객 EXE에 포함하지 않습니다. 고객이 키를 입력할 필요가 없습니다.
프로그램의 만료일은 컴파일된 EX5/DLL에 들어가고, 서명된 license.json은 설치 폴더에 자동 배치됩니다.

제한
- 온라인 인증, 중앙 폐기, 계좌/하드웨어 바인딩을 구현하지 않았습니다.
- PC 시계/시간대 조작을 완전히 방지하는 DRM이 아닙니다. PC 시각을 정상 동기화해 사용하세요.
- 컴파일된 파일의 복사 사용은 만료일 범위에서 가능합니다. 계좌 바인딩 프리셋은 연결 안전 설정이지 DRM이 아닙니다.
- RSA 패키지 서명은 Windows Authenticode 게시자 서명과 다릅니다.
  이 도구/설치본에 상용 코드서명 인증서는 포함되어 있지 않습니다.
- 보안 경고가 발생하면 발급자와 SHA-256을 확인하세요. 백신/SmartScreen을 끄도록 요구하지 않습니다.
`
const ThirdPartyNotices = `Divine Shield Deployment Toolkit

This executable contains a Go standard-library runtime. Go license text follows.
Trading program copyrights remain with their original author (Oilve Oil).
No MetaTrader, NinjaTrader, broker DLLs, commercial SDKs, or fonts are redistributed by this toolkit.

Copyright (c) 2009 The Go Authors. All rights reserved.
Redistribution and use in source and binary forms, with or without modification, are
permitted provided that the following conditions are met:
* Redistributions of source code must retain the above copyright notice, this list
  of conditions and the following disclaimer.
* Redistributions in binary form must reproduce the above copyright notice, this
  list of conditions and the following disclaimer in the documentation and/or other
  materials provided with the distribution.
* Neither the name of Google Inc. nor the names of its contributors may be used to
  endorse or promote products derived from this software without specific prior
  written permission.
THIS SOFTWARE IS PROVIDED BY THE COPYRIGHT HOLDERS AND CONTRIBUTORS "AS IS" AND
ANY EXPRESS OR IMPLIED WARRANTIES, INCLUDING, BUT NOT LIMITED TO, THE IMPLIED
WARRANTIES OF MERCHANTABILITY AND FITNESS FOR A PARTICULAR PURPOSE ARE DISCLAIMED.
IN NO EVENT SHALL THE COPYRIGHT OWNER OR CONTRIBUTORS BE LIABLE FOR ANY DIRECT,
INDIRECT, INCIDENTAL, SPECIAL, EXEMPLARY, OR CONSEQUENTIAL DAMAGES (INCLUDING,
BUT NOT LIMITED TO, PROCUREMENT OF SUBSTITUTE GOODS OR SERVICES; LOSS OF USE,
DATA, OR PROFITS; OR BUSINESS INTERRUPTION) HOWEVER CAUSED AND ON ANY THEORY OF
LIABILITY, WHETHER IN CONTRACT, STRICT LIABILITY, OR TORT (INCLUDING NEGLIGENCE
OR OTHERWISE) ARISING IN ANY WAY OUT OF THE USE OF THIS SOFTWARE, EVEN IF ADVISED
OF THE POSSIBILITY OF SUCH DAMAGE.
`
