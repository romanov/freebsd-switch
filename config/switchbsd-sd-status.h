/* SPDX-License-Identifier: BSD-2-Clause */
#ifndef SWITCHBSD_SD_STATUS_H
#define SWITCHBSD_SD_STATUS_H

#define SWITCHBSD_SD_STATUS_GUID \
  { 0x47dfdfab, 0x8c97, 0x44ce, { 0x85, 0xae, 0xdb, 0x01, 0xab, 0xb6, 0xd5, 0x03 } }
#define SWITCHBSD_SD_STATUS_VERSION 2

enum {
  SwitchSdClock = 1, SwitchSdPmic, SwitchSdProbe, SwitchSdHost,
  SwitchSdCardInit, SwitchSdCardFinalize, SwitchSdCapacity,
  SwitchSdReadSector, SwitchSdPublish, SwitchSdReady
};

typedef struct {
  UINT32 Version;
  UINT32 Stage;
  EFI_STATUS Status;
  INT32 CardResult;
  UINT32 BlockSize;
  UINT64 Blocks;
  UINT32 NoIoPowerBefore;
  UINT32 NoIoPowerAfter;
  UINT32 PowerDetect;
  UINT32 CommandCount;
  UINT32 LastCommand;
  UINT32 LastArgument;
  INT32 LastCommandResult;
  UINT32 LastResponse;
  UINT32 CommandInterrupt;
  UINT32 PresentState;
  UINT32 ClockControl;
  UINT32 PowerControl;
  UINT32 FirstErrorCommand;
  INT32 FirstErrorResult;
} SWITCHBSD_SD_STATUS;
#endif
