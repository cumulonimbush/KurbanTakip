[Setup]
; Temel Uygulama Bilgileri
AppName=Kurban Takip Sistemi
AppVersion=2.1.0
AppPublisher=akk/Þirket
DefaultDirName={autopf}\Kurban Takip
DefaultGroupName=Kurban Takip Sistemi
OutputDir=.\Output
OutputBaseFilename=KurbanTakip_Kurulum_v2.1.0
Compression=lzma2
SolidCompression=yes
; Kurulum sihirbazýnýn dilini Türkçe yapmak için (varsa)
; Language=Turkish

[Tasks]
Name: "desktopicon"; Description: "Masaüstüne kýsayol oluþtur"; GroupDescription: "Ek Kýsayollar:"

[Files]
; DÝKKAT: Buradaki "C:\Senin\Proje\Yolun\main.dist" kýsmýný KENDÝ yolunla deðiþtir.
Source: "C:\Users\Ali Kemal\mystuff\KurbanTakip\src\main.dist\main.exe"; DestDir: "{app}"; Flags: ignoreversion
Source: "C:\Users\Ali Kemal\mystuff\KurbanTakip\src\main.dist\*"; DestDir: "{app}"; Flags: ignoreversion recursesubdirs createallsubdirs

[Icons]
Name: "{group}\Kurban Takip Sistemi"; Filename: "{app}\main.exe"
Name: "{autodesktop}\Kurban Takip Sistemi"; Filename: "{app}\main.exe"; Tasks: desktopicon

[Run]
Filename: "{app}\main.exe"; Description: "Kurban Takip Sistemini Baþlat"; Flags: nowait postinstall skipifsilent