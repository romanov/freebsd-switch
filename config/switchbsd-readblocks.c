/* SPDX-License-Identifier: GPL-2.0-or-later */
EFI_STATUS
EFIAPI
MMCHSReadBlocks (
    IN EFI_BLOCK_IO_PROTOCOL *This,
    IN UINT32 MediaId,
    IN EFI_LBA Lba,
    IN UINTN BufferSize,
    OUT VOID *Buffer
)
{
    BIO_INSTANCE *Instance = BIO_INSTANCE_FROM_BLOCKIO_THIS(This);
    EFI_BLOCK_IO_MEDIA *Media = &Instance->BlockMedia;
    UINTN Blocks;

    if (!Media->MediaPresent) return EFI_NO_MEDIA;
    if (MediaId != Media->MediaId) return EFI_MEDIA_CHANGED;
    if (BufferSize == 0) return EFI_SUCCESS;
    if (Media->BlockSize == 0) return EFI_DEVICE_ERROR;
    if (BufferSize % Media->BlockSize != 0) return EFI_BAD_BUFFER_SIZE;
    if (Buffer == NULL || (Media->IoAlign > 1 &&
        ((UINTN)Buffer & (Media->IoAlign - 1)) != 0)) return EFI_INVALID_PARAMETER;
    Blocks = BufferSize / Media->BlockSize;
    if (Lba > Media->LastBlock || Blocks - 1 > Media->LastBlock - Lba)
        return EFI_INVALID_PARAMETER;
    // MmcReadInternal takes a 32-bit byte count and byte address arithmetic.
    if (BufferSize > MAX_UINT32 || Lba > MAX_UINT64 / Media->BlockSize)
        return EFI_BAD_BUFFER_SIZE;
    return MmcReadInternal(Instance, Lba * Media->BlockSize, Buffer,
                           (UINT32)BufferSize) == 1 ? EFI_SUCCESS : EFI_DEVICE_ERROR;
}
