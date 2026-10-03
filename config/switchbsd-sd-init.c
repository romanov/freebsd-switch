/* SPDX-License-Identifier: GPL-2.0-or-later
 * Replacement entry point for the pinned Switch SD driver.
 * The table is allocated separately from the driver image: it remains valid
 * even if DXE unloads the driver after a failed entry point.
 */
EFI_STATUS
EFIAPI
SdMmcDxeInitialize (
    IN EFI_HANDLE ImageHandle,
    IN EFI_SYSTEM_TABLE *SystemTable
)
{
    EFI_STATUS Status;
    EFI_STATUS TableStatus;
    EFI_GUID DiagGuid = SWITCHBSD_SD_STATUS_GUID;
    SWITCHBSD_SD_STATUS *Diag;
    BIO_INSTANCE *Instance = NULL;
    UINT8 Sector[512];
    int Ret;

    Diag = AllocateZeroPool(sizeof(*Diag));
    if (Diag == NULL) return EFI_OUT_OF_RESOURCES;
    Diag->Version = SWITCHBSD_SD_STATUS_VERSION;
    Diag->Status = EFI_NOT_READY;
    TableStatus = gBS->InstallConfigurationTable(&DiagGuid, Diag);
    if (EFI_ERROR(TableStatus)) {
        FreePool(Diag);
        return TableStatus;
    }
    mSwitchSdDiag = Diag;

    Diag->Stage = SwitchSdClock;
    Status = gBS->LocateProtocol(&gTegraUBootClockManagementProtocolGuid,
                                 NULL, (VOID **)&mClkProtocol);
    if (EFI_ERROR(Status)) goto exit;
    Diag->Stage = SwitchSdPmic;
    Status = gBS->LocateProtocol(&gPmicProtocolGuid, NULL, (VOID **)&mPmicProtocol);
    if (EFI_ERROR(Status)) goto exit;
    Diag->Stage = SwitchSdProbe;
    Status = SdControllerProbe();
    if (EFI_ERROR(Status)) goto exit;
    Diag->Stage = SwitchSdHost;
    Status = TegraMmcInit();
    if (EFI_ERROR(Status)) goto exit;
    Diag->Stage = SwitchSdCardInit;
    Ret = SdFxInit();
    Diag->CardResult = Ret;
    if (Ret != 0) { Status = EFI_DEVICE_ERROR; goto exit; }
    Diag->Stage = SwitchSdCardFinalize;
    Ret = SdFxInitFinalize();
    Diag->CardResult = Ret;
    if (Ret != 0) { Status = EFI_DEVICE_ERROR; goto exit; }

    Diag->Stage = SwitchSdCapacity;
    Diag->Blocks = mBlkDesc.lba;
    Diag->BlockSize = mBlkDesc.blksz;
    if (mMmcInstance.has_init != 1 || mBlkDesc.lba == 0 ||
        mBlkDesc.blksz != sizeof(Sector)) {
        Status = EFI_UNSUPPORTED;
        goto exit;
    }
    Diag->Stage = SwitchSdReadSector;
    Ret = mmc_bread(0, 1, Sector);
    Diag->CardResult = Ret;
    if (Ret != 1) { Status = EFI_DEVICE_ERROR; goto exit; }
    // Partition and FAT drivers interpret sector zero. Do not dead-loop on a
    // missing MBR signature, or scan unrelated sectors looking for one.
    Diag->Stage = SwitchSdPublish;
    Status = BioInstanceContructor(&Instance);
    if (EFI_ERROR(Status)) goto exit;
    Instance->BlockMedia.BlockSize = mBlkDesc.blksz;
    Instance->BlockMedia.LastBlock = mBlkDesc.lba - 1;
    Status = gBS->InstallMultipleProtocolInterfaces(
        &Instance->Handle,
        &gEfiBlockIoProtocolGuid, &Instance->BlockIo,
        &gEfiDevicePathProtocolGuid, &Instance->DevicePath, NULL);
    if (EFI_ERROR(Status)) { FreePool(Instance); goto exit; }
    Diag->Stage = SwitchSdReady;
exit:
    Diag->Status = Status;
    DEBUG((DEBUG_INFO, "SWITCHBSD: SD stage=%u status=%r card=%d blocks=%Lu\n",
           Diag->Stage, Status, Diag->CardResult, Diag->Blocks));
    return Status;
}
