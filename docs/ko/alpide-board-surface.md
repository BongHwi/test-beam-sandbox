[English](../alpide-board-surface.md) | [Korean](../ko/alpide-board-surface.md) | [Japanese](../ja/alpide-board-surface.md)

# 공개 보드 호출 표면

이 시스템의 응답은 합성값이며 실제 장비의 측정값이 아닙니다. [공식 Corryvreckan 저장소](https://gitlab.cern.ch/corryvreckan/corryvreckan)의 master commit `22fca3e43ceec7ba6263a5cf179cd57d5fff42fe`로 빌드합니다.

## 소스 인터페이스

정적 `self.daq` 속성 접근과 소스 행 번호는 [공개 ALPIDE producer](https://github.com/eudaq/eudaq/blob/bfe26c7b73844344b2cbfa37ab853f2a0cb1f23e/user/ITS3/python/ALPIDEProducer.py)에서 추출했습니다.

| Attribute | Source lines |
|---|---|
| `alpide_cmd_issue` | 98, 116, 120 |
| `alpide_reg_read` | 230 |
| `alpide_reg_write` | 106, 107, 108, 109, 110, 111, 117, 118, 119, 123, 129 |
| `carrier_temp` | 208 |
| `event_read` | 237 |
| `evtbld` | 88 |
| `evtbld.rst` | 88 |
| `evtbld.rst.issue` | 88 |
| `evtpkr` | 87 |
| `evtpkr.rst` | 87 |
| `evtpkr.rst.issue` | 87 |
| `get_fpga_git` | 216 |
| `get_fpga_tcompile` | 217 |
| `get_software_diff` | 225 |
| `get_software_git` | 224 |
| `power_on` | 81 |
| `power_status` | 207 |
| `purge_events` | 90 |
| `rdoctrl` | 85, 124, 125, 126 |
| `rdoctrl.chipid` | 125 |
| `rdoctrl.chipid.write` | 125 |
| `rdoctrl.ctrl` | 126 |
| `rdoctrl.ctrl.write` | 126 |
| `rdoctrl.delay_set` | 124 |
| `rdoctrl.delay_set.write` | 124 |
| `rdoctrl.rst` | 85 |
| `rdoctrl.rst.issue` | 85 |
| `rdomux` | 127, 131 |
| `rdomux.ctrl` | 127, 131 |
| `rdomux.ctrl.write` | 127, 131 |
| `rdopar` | 86, 94, 101, 130 |
| `rdopar.clr` | 94, 101 |
| `rdopar.clr.issue` | 94, 101 |
| `rdopar.ctrl` | 130 |
| `rdopar.ctrl.write` | 130 |
| `rdopar.rst` | 86 |
| `rdopar.rst.issue` | 86 |
| `trg` | 84, 89, 136, 137, 190, 195, 199, 203 |
| `trg.ctrl` | 190, 195, 199, 203 |
| `trg.ctrl.write` | 190, 195, 199, 203 |
| `trg.fixedbusy` | 137 |
| `trg.fixedbusy.write` | 137 |
| `trg.minspacing` | 136 |
| `trg.minspacing.write` | 136 |
| `trg.opcode` | 84 |
| `trg.opcode.write` | 84 |
| `trg.rstrdobsy` | 89 |
| `trg.rstrdobsy.issue` | 89 |
| `trgmon` | 95, 102, 231, 232, 233 |
| `trgmon.clr` | 95, 102 |
| `trgmon.clr.issue` | 95, 102 |
| `trgmon.lat` | 231 |
| `trgmon.lat.issue` | 231 |
| `trgmon.regs` | 232, 233 |
| `trgmon.regs[reg].read` | 233 |
| `trgseq` | 192, 193, 201 |
| `trgseq.dt_set` | 192 |
| `trgseq.dt_set.write` | 192 |
| `trgseq.ntrg_set` | 193 |
| `trgseq.ntrg_set.write` | 193 |
| `trgseq.stop` | 201 |
| `trgseq.stop.issue` | 201 |
| `xonxoff` | 91, 132 |
| `xonxoff.ctrl` | 132 |
| `xonxoff.ctrl.write` | 132 |
| `xonxoff.rst` | 91 |
| `xonxoff.rst.issue` | 91 |

