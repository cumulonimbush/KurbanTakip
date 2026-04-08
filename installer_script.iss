[Setup]
; Temel Uygulama Bilgileri
AppName=Kurban Takip Sistemi
AppVersion=2.2.1
AppPublisher=Senin Adın/Şirketin
DefaultDirName={autopf}\Kurban Takip
DefaultGroupName=Kurban Takip Sistemi
OutputDir=.\Output
OutputBaseFilename=KurbanTakip_Kurulum_v2.2.1
Compression=lzma2
SolidCompression=yes

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kısayol oluştur"; GroupDescription: "Ek Kısayollar:"

[Files]
; DİKKAT: main.dist klasörünün yolunu kendi bilgisayarına göre kontrol et. (Sonundaki \* işaretini silme!)
Source: "C:\Workspace\Projects\KurbanTakip\src\main.py"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Workspace\Projects\KurbanTakip\src\main.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Kurban Takip Sistemi"; Filename: "{app}\main.exe"
Name: "{autodesktop}\Kurban Takip Sistemi"; Filename: "{app}\main.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\main.exe"; Description: "Kurban Takip Sistemini Başlat"; Flags: nowait postinstall skipifsilent

