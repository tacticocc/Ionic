; Replaces the WinShell 20121005 plug-in with Windows Shell COM calls made
; through NSIS's zlib-licensed System plug-in. Keep this file in source form.

!include "LogicLib.nsh"
!include "Win\COM.nsh"
!include "Win\Propkey.nsh"

!ifndef CLSID_ApplicationDestinations
  !define CLSID_ApplicationDestinations {86c14003-4d6b-4ef3-a7b4-0506663b2e68}
!endif
!ifndef IID_IApplicationDestinations
  !define IID_IApplicationDestinations {12337D35-94C6-48A0-BCE7-6A9C69D4D600}
  ${NSISCOMIFACEDECL}IApplicationDestinations SetAppID 3 (w)i
  ${NSISCOMIFACEDECL}IApplicationDestinations RemoveDestination 4 (p)i
  ${NSISCOMIFACEDECL}IApplicationDestinations RemoveAllDestinations 5 ()i
!endif

!ifndef CLSID_DestinationList
  !define CLSID_DestinationList {77f10cf0-3db5-4966-b520-b7c54fd35ed6}
!endif
!ifndef IID_ICustomDestinationList
  !define IID_ICustomDestinationList {6332debf-87b5-4670-90c0-5e57b408a49e}
  ${NSISCOMIFACEDECL}ICustomDestinationList SetAppID 3 (w)i
  ${NSISCOMIFACEDECL}ICustomDestinationList BeginList 4 (*i,g,*p)i
  ${NSISCOMIFACEDECL}ICustomDestinationList AppendCategory 5 (w,p)i
  ${NSISCOMIFACEDECL}ICustomDestinationList AppendKnownCategory 6 (i)i
  ${NSISCOMIFACEDECL}ICustomDestinationList AddUserTasks 7 (p)i
  ${NSISCOMIFACEDECL}ICustomDestinationList CommitList 8 ()i
  ${NSISCOMIFACEDECL}ICustomDestinationList GetRemovedDestinations 9 (g,*p)i
  ${NSISCOMIFACEDECL}ICustomDestinationList DeleteList 10 (w)i
  ${NSISCOMIFACEDECL}ICustomDestinationList AbortList 11 ()i
!endif

!ifndef BUILD_UNINSTALLER
!define TacticoSetShortcutAppId "!insertmacro TacticoSetShortcutAppId "
!macro TacticoSetShortcutAppId shortcut appId
  Push "${shortcut}"
  Push "${appId}"
  Call TacticoSetShortcutAppId
!macroend

Function TacticoSetShortcutAppId
  Exch $1
  Exch 1
  Exch $0
  Push $2
  Push $3
  Push $4
  Push $5
  Push $6
  Push $7

  StrCpy $2 0
  StrCpy $3 0
  StrCpy $4 0
  StrCpy $5 0
  !insertmacro ComHlpr_CreateInProcInstance ${CLSID_ShellLink} ${IID_IShellLink} r2 ""
  ${If} $2 P<> 0
    ${IUnknown::QueryInterface} $2 '("${IID_IPersistFile}",.r3)'
    ${If} $3 P<> 0
      ${IPersistFile::Load} $3 '("$0",2).r6'
      ${If} $6 = 0
        ${IUnknown::QueryInterface} $2 '("${IID_IPropertyStore}",.r4)'
        ${If} $4 P<> 0
          System::Call '*${SYSSTRUCT_PROPERTYKEY}(${PKEY_AppUserModel_ID})p.r5'
          System::Call '*${SYSSTRUCT_PROPVARIANT}(0,,0)p.r6'
          System::Call 'PROPSYS::InitPropVariantFromString(w "$1",p r6)i.r7'
          ${If} $7 = 0
            ${IPropertyStore::SetValue} $4 '($5,$6).r7'
            ${If} $7 = 0
              ${IPropertyStore::Commit} $4 ""
              ${IPersistFile::Save} $3 '("$0",1)'
            ${EndIf}
            System::Call 'OLE32::PropVariantClear(p r6)'
          ${EndIf}
          System::Free $6
          System::Free $5
          ${IUnknown::Release} $4 ""
        ${EndIf}
      ${EndIf}
      ${IUnknown::Release} $3 ""
    ${EndIf}
    ${IUnknown::Release} $2 ""
  ${EndIf}

  Pop $7
  Pop $6
  Pop $5
  Pop $4
  Pop $3
  Pop $2
  Pop $0
  Pop $1
FunctionEnd

!define TacticoUnpinShortcut "!insertmacro TacticoUnpinShortcut "
!macro TacticoUnpinShortcut shortcut
  Push "${shortcut}"
  Call TacticoUnpinShortcut
!macroend

Function TacticoUnpinShortcut
  Exch $0
  Push $1
  Push $2
  StrCpy $1 0
  StrCpy $2 0
  !insertmacro ComHlpr_CreateInProcInstance ${CLSID_StartMenuPin} ${IID_IStartMenuPinnedList} r1 ""
  ${If} $1 P<> 0
    System::Call 'SHELL32::SHCreateItemFromParsingName(w r0,p0,g"${IID_IShellItem}",*p.r2)'
    ${If} $2 P<> 0
      ${IStartMenuPinnedList::RemoveFromList} $1 '(r2)'
      ${IUnknown::Release} $2 ""
    ${EndIf}
    ${IUnknown::Release} $1 ""
  ${EndIf}
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

!else
!define TacticoUnpinShortcut "!insertmacro TacticoUnpinShortcut "
!macro TacticoUnpinShortcut shortcut
  Push "${shortcut}"
  Call un.TacticoUnpinShortcut
!macroend

Function un.TacticoUnpinShortcut
  Exch $0
  Push $1
  Push $2
  StrCpy $1 0
  StrCpy $2 0
  !insertmacro ComHlpr_CreateInProcInstance ${CLSID_StartMenuPin} ${IID_IStartMenuPinnedList} r1 ""
  ${If} $1 P<> 0
    System::Call 'SHELL32::SHCreateItemFromParsingName(w r0,p0,g"${IID_IShellItem}",*p.r2)'
    ${If} $2 P<> 0
      ${IStartMenuPinnedList::RemoveFromList} $1 '(r2)'
      ${IUnknown::Release} $2 ""
    ${EndIf}
    ${IUnknown::Release} $1 ""
  ${EndIf}
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

!define TacticoClearAppDestinations "!insertmacro TacticoClearAppDestinations "
!macro TacticoClearAppDestinations appId
  Push "${appId}"
  Call un.TacticoClearAppDestinations
!macroend

Function un.TacticoClearAppDestinations
  Exch $0
  Push $1
  Push $2
  StrCpy $1 0
  !insertmacro ComHlpr_CreateInProcInstance ${CLSID_DestinationList} ${IID_ICustomDestinationList} r1 ""
  ${If} $1 P<> 0
    ${ICustomDestinationList::DeleteList} $1 '("$0")'
    ${IUnknown::Release} $1 ""
  ${EndIf}
  StrCpy $1 0
  !insertmacro ComHlpr_CreateInProcInstance ${CLSID_ApplicationDestinations} ${IID_IApplicationDestinations} r1 ""
  ${If} $1 P<> 0
    ${IApplicationDestinations::SetAppID} $1 '("$0").r2'
    ${If} $2 >= 0
      ${IApplicationDestinations::RemoveAllDestinations} $1 ""
    ${EndIf}
    ${IUnknown::Release} $1 ""
  ${EndIf}
  Pop $2
  Pop $1
  Pop $0
FunctionEnd

!endif
