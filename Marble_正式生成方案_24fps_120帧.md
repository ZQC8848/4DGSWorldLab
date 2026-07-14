# Marble 正式生成方案 · 24 fps × 120 帧(复用测试成果)

> 上游文档:《Marble_10帧测试计划.md》(P0 测试已完成,10/10 成功)
> 本文档:正式批量生成的执行方案。**核心决策:24 fps × 5 秒 = 120 帧,复用已生成的 10 个世界,新生成 110 帧。**
> 日期:2026-07-13

---

## 1. 方案选型

预算 250,000 credits,源视频 `7月10日(1).mp4`(4320×2160 equirect,30 fps,5 秒,150 源帧)。

| 方案 | 总帧数 | 可复用 | 新生成 | 花费 | 剩余 | 判断 |
|---|---|---|---|---|---|---|
| 15 fps | 75 | 仅 5 帧 | 70 | 105,000 | 145,000 | 时间网格与已有帧对不齐,复用差 |
| **24 fps** | **120** | **10 帧全部** | **110** | **165,000** | **85,000 (34%)** | ✅ **采用** |
| 30 fps | 150 | 10 帧全部 | 140 | 210,000 | 40,000 (16%) | 余量太薄,收益递减 |

**选 24 fps 的理由:**
1. 播放性能已实测验证:切换→可见延迟最坏 17ms,24fps 帧预算 41.7ms,余量 2.4×
2. 剩余 85,000 credits 给正式 hackathon 内容(很可能重新生成 AI 全景视频)留足迭代弹药
3. 24→30 fps 感知收益递减,但成本差 45,000
4. 已知瑕疵:30/24 不整除,抽帧节奏为源帧步长 1,1,1,2 循环,运动有极轻微节奏抖动,demo 不可察觉

---

## 2. 复用映射(已生成的 10 个世界)

已有 10 帧位于 0.5 秒整数倍时间点,24fps 网格(t = k/24)精确包含这些点,且两种网格在这些时刻选中**同一源帧**(已验证:12fps 第 6k 帧与 24fps 第 12k 帧同取源帧 floor(15k)),生成输入完全一致,可直接复用。

| 时刻 (s) | 旧帧名 (12fps 网格) | 新帧名 (24fps 网格) | world_id 出处 |
|---|---|---|---|
| 0.0 | frame_0000 | frame_0000 | worlds_test/frame_0000 |
| 0.5 | frame_0006 | frame_0012 | worlds_test/frame_0006 |
| 1.0 | frame_0012 | frame_0024 | worlds_test/frame_0012 |
| 1.5 | frame_0018 | frame_0036 | worlds_test/frame_0018 |
| 2.0 | frame_0024 | frame_0048 | worlds_test/frame_0024 |
| 2.5 | frame_0030 | frame_0060 | worlds_test/frame_0030 |
| 3.0 | frame_0036 | frame_0072 | worlds_test/frame_0036 |
| 3.5 | frame_0042 | frame_0084 | worlds_test/frame_0042 |
| 4.0 | frame_0048 | frame_0096 | worlds_test/frame_0048 |
| 4.5 | frame_0054 | frame_0108 | worlds_test/frame_0054 |

**复用价值:15,000 credits + 约 1 小时生成时间。**

---

## 3. 执行流程

### Step 1 · 24fps 抽帧
```
ffmpeg -i "7月10日(1).mp4" -vf fps=24 -start_number 0 frames_24fps/frame_%04d.png
```
- 产出 120 帧,`frame_0000.png` ~ `frame_0119.png`
- **校验复用前提**:对比 10 个复用点的新旧 PNG 哈希(如 `frames_24fps/frame_0012.png` vs `frames_test10/frame_0006.png`),必须逐字节一致;不一致则该帧不复用、按新帧生成

### Step 2 · 迁移 manifest(复用登记)
- 新输出目录 `worlds_24fps/`,新建 manifest(model=marble-1.1,seed=12345,与测试批一致——**这是复用合法性的前提**)
- 把 10 个旧世界按 §2 映射表登记进新 manifest:改用新帧名,携带原 world_id / media_asset_id / cost,状态直接标 `downloaded`
- 资产文件(world.spz / world_500k.spz / metadata.json)从 `worlds_test/<旧名>/` 复制到 `worlds_24fps/<新名>/`

### Step 3 · 批量生成 110 帧
- 复用 `marble_generate.py`:`python marble_generate.py --input-dir frames_24fps --output-dir worlds_24fps`
- 脚本自带:429 指数退避 + 3s 提交间隔、manifest 断点续跑、计费核对(≠1500 告警)、full_res 下载
- 已登记为 `downloaded` 的 10 帧自动跳过,只跑缺失的 110 帧
- 预计时长 2–3 小时(单帧约 6 分钟,云端并发);中断随时重跑,不重复扣费
- 完成后跑 `download_500k.py`(改指向 worlds_24fps)补 500k 播放档

### Step 4 · 验收
- [ ] 120/120 状态 downloaded,总新增扣费 = 110 × 1,500 = 165,000
- [ ] 每帧有 world.spz(full_res ~27MB)+ world_500k.spz(~7.4MB)+ metadata.json
- [ ] 汇总 120 帧的 `metric_scale_factor` / `ground_plane_offset`,确认波动仍在 ±2% 量级(配准输入)
- [ ] Spark 查看器把 FRAMES 列表换成 120 帧,24fps 播放目测连贯性

---

## 4. 预算总账

| 项 | credits |
|---|---|
| 已花费(10 帧测试) | 15,000 |
| 本方案新生成 110 帧 | 165,000 |
| **累计** | **180,000** |
| **剩余** | **70,000**(≈46 次生成,供废帧重生成 + 正式内容迭代) |

> 注:若按"当前余额恰为 250,000"计,则剩余 85,000;两种口径差异即测试批的 15,000,以 platform.worldlabs.ai 实际余额为准。

---

## 5. 风险与对策

| 风险 | 对策 |
|---|---|
| 抽帧哈希不一致导致复用失效 | Step 1 逐帧校验;失效帧退回正常生成(每帧多花 1,500,上限 10 帧) |
| 批量 429 限流 | 脚本已带指数退避;若整批卡住,分 3–4 批各 ~30 帧提交 |
| 废帧(内容质量差) | 剩余 70,000+ 预算覆盖;重生成时换 seed 需整批评估一致性影响 |
| 120 帧全预载内存(~890MB @500k 档) | Spark 验证阶段可接受;P3 正式播放器按笔记 §7 改环形预取窗口 |
| 24fps 抽帧节奏抖动(1,1,1,2) | 已知且接受;如最终不可忍受,升 30fps 补 30 帧(45,000,预算可覆盖) |

---

## 6. 执行清单(按序)

- [x] Step 1:24fps 抽帧 + 哈希校验
- [x] Step 2:manifest 迁移 + 资产复制
- [x] Step 3:批量生成 110 帧(后台,断点续跑)
- [x] Step 3b:补 500k 播放档
- [x] Step 4:验收四项
- [x] git 提交(manifest/metadata 入库,spz 走 .gitignore)

## 7. 执行结果(2026-07-13/14)

- **120/120 帧就绪**;累计扣费 **180,000**(120 × 1,500,含复用 10 帧的 15,000)
- 哈希校验发现两次 ffmpeg 抽帧存在抖动级像素差(PSNR 55.8dB,同源帧,不可辨)→ 决策:用原测试 PNG 覆盖 10 个复用位,保证"输入图 ↔ 复用世界"严格对应,复用照常
- 批量过程失败共 20 帧次(网络中断 8 + 云端 500 共 12),全部经断点重跑恢复,失败未扣费
- 资产:full_res 26.6–27.4MB/帧(共 3.2GB),500k 档共 838MB;`semantics_metadata` 120/120 齐全
- 尺度统计(120 帧):`metric_scale_factor` 1.3565–1.4970(±4.8%),`ground_plane_offset` 1.5118–1.5865(±2.4%)→ 配准按逐帧查表归一化
- Spark 查看器已升级 120 帧 @ 24fps 默认,全部预载(JS 堆 ~943MB),`http://localhost:8931/spark_viewer/`
