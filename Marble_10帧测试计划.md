# Marble API · 10 帧 3DGS 生成测试计划

> 上游方案:Obsidian 笔记《AI全景_逐帧3DGS_4DGS方案》(路线 A:逐帧独立 3DGS → 配准 → 切换播放)
> 本文档:P0/P1 阶段的 10 帧小批测试——验证 Marble API 全链路,为 120 帧正式批量做准备。
> 日期:2026-07-13

---

## 1. 当前进度

| 项 | 状态 |
|---|---|
| 源视频 `7月10日(1).mp4`(4320×2160 equirect,30fps,约 5s) | ✅ 已就绪 |
| 12fps 抽帧 → `frames_12fps/`(60 帧,`frame_0000.png` ~ `frame_0059.png`,PNG 无损) | ✅ 完成 |
| 均匀取 10 帧 → `frames_test10/` | ✅ 完成 |
| World Labs API 文档调研 | ✅ 完成(见 §3) |
| API key 配置(`.env` 文件) | ✅ 完成 |
| 测试脚本编写(`marble_generate.py`) | ✅ 完成 |
| 10 帧生成 + spz 下载 → `worlds_test/` | ✅ 完成(2026-07-13,共 15,000 credits) |

### 测试结果(2026-07-13)

- **10/10 帧成功**,每帧扣费恰好 1,500,总计 15,000 credits,无隐藏费用
- 单帧生成耗时约 6 分钟;9 帧并发提交约 20 分钟内全部完成
- 每帧提供 4 档分辨率:`100k / 150k / 500k / full_res`;已下载 full_res(约 27 MB/帧,估算 ~190 万 splat)。**`500k` 档(约 7 MB)正好落在方案剪枝目标 30–80 万区间,P3 播放器可直接用,免做剪枝**;各档 URL 存于每帧 metadata.json
- **`semantics_metadata` 跨帧稳定**:`metric_scale_factor` 范围 1.419–1.481(±2%),`ground_plane_offset` 范围 1.517–1.573(±1.8%)→ P2 配准的尺度归一化和垂直对齐可直接查表,剩余自由度只有水平平移 + yaw
- 踩坑记录:`is_pano` 须传 JSON 布尔(字符串 `"true"` 会 422);最高分辨率 key 是 `full_res`;批量提交会触发 HTTP 429 限流(脚本已加指数退避重试 + 3s 提交间隔)

---

## 2. 选帧方案

从 60 帧中按**步长 6** 均匀抽取 10 帧,帧间隔 0.5 秒,覆盖整段视频的时间演化,便于评估跨帧漂移:

```
frame_0000, frame_0006, frame_0012, frame_0018, frame_0024,
frame_0030, frame_0036, frame_0042, frame_0048, frame_0054
```

已复制到 `frames_test10/`,保留原文件名以与 `frames_12fps/` 源帧对应。单帧 PNG 约 14–19.4 MB。

---

## 3. API 调用链路(已从 docs.worldlabs.ai 官方文档确认)

- **Base URL**:`https://api.worldlabs.ai/marble/v1`
- **认证**:请求头 `WLT-Api-Key: <key>`
- **注意**:API credits 必须在 platform.worldlabs.ai 购买,Marble app 内的 credits 不能用于 API。

### 每帧四步

**Step 1 · 上传图片**
`POST /media-assets:prepare_upload`

```json
{ "file_name": "frame_0006.png", "extension": "png", "kind": "image" }
```

返回 `media_asset_id` + 预签名 `upload_url`(含 `upload_method: PUT` 和 `required_headers`)。
用 `PUT` 将 PNG 直传到该 URL(文件不经过 API 转发,~19MB 无压力)。

**Step 2 · 发起生成**
`POST /worlds:generate`

```json
{
  "display_name": "frame_0006",
  "model": "marble-1.1",
  "seed": 12345,
  "world_prompt": {
    "type": "image",
    "image_prompt": { "source": "media_asset", "media_asset_id": "..." },
    "is_pano": "true"
  }
}
```

关键取值:
- `model: marble-1.1` — 固定价 1,500 credits/次;**不用 1.1-plus**(浮动计费 + 破坏跨帧一致性)
- `seed`:全部 10 帧统一固定(消除同输入随机性)
- `is_pano: "true"` — 显式强制按 equirectangular 处理,不用 `"auto"`,避免个别帧检测失败导致行为不一致;有效全景输入会跳过 pano 生成 = 0 额外 credits
- 返回 `operation_id`

**Step 3 · 轮询**
`GET /operations/{operation_id}`

- 直到 `done: true`;`metadata.progress_percentage` 可看进度
- 完成后 `response` 内含 World 对象;**记录 `cost.total_credits` 核对计费是否为 1,500**
- 失败时 `error` 字段有详情;HTTP 402 = credits 不足

**Step 4 · 下载资产**
从 operation response(或 `GET /worlds/{world_id}`)的 `assets` 字段:

| 资产 | 字段 | 用途 |
|---|---|---|
| Gaussian Splats | `assets.splats.spz_urls`(按分辨率索引) | **主产物**,下载最高分辨率 |
| 语义元数据 | `assets.splats.semantics_metadata` | **必存**——含公制尺度 + 地面对齐信息,是 P2 配准阶段现成的尺度/坐标锚,可能大幅减少 ICP 工作量 |
| Collider mesh | `assets.mesh.collider_mesh_url` | 可选,Unity 阶段物理用 |
| Pano | `assets.imagery.pano_url` | 可选,调试对照 |

---

## 4. 测试脚本设计

单个 Python 脚本(标准库 + `requests`),要点:

- **API key 从环境变量 `WLT_API_KEY` 读取**,绝不硬编码进脚本
- **manifest.json 状态机**:记录每帧 `media_asset_id` / `operation_id` / `world_id` / 实际扣费 / 状态(uploaded → submitted → done → downloaded),脚本可中断重跑,已提交的不重复提交,只补轮询和下载。**该 manifest 之后 120 帧正式批量直接复用**
- **并发提交、统一轮询**:10 个生成任务先全部提交(云端排队),再循环轮询,不串行干等
- 全部帧统一 seed / model / 参数(方案 §5 Step 2 的硬要求)
- 输出结构:

```
worlds_test/
  frame_0000/
    world.spz          # 最高分辨率 splats
    metadata.json      # semantics_metadata + cost + world_id + operation_id
  frame_0006/
    ...
  manifest.json        # 全局状态
```

### 执行策略

**先只跑 1 帧(frame_0000)验证全链路**——上传 → 生成 → 轮询 → 下载 → 确认扣费 1,500 → spz 能在 Spark 里打开——确认无误后再放剩余 9 帧。对应笔记 §4 "先花 3–5 帧跑通全链路"的原则。

---

## 5. 预算

| 项 | credits |
|---|---|
| 本次测试:10 帧 × 1,500 | **15,000**(约 $12,占总预算 6%) |
| 后续正式:120 帧 × 1,500 | 180,000 |
| 总预算 | 250,000 |
| 测试 + 正式后剩余 | 55,000(≈36 帧余量,用于废帧重生成) |

---

## 6. 测试要验证的问题清单

1. ✅/❌ 全景 PNG 能否被 Marble 正确识别为 equirect(`is_pano: "true"`)
2. 实际扣费是否 = 1,500/帧(无隐藏 pano 生成费)
3. 单帧生成耗时(决定 120 帧批量的总时长与并发策略)
4. spz 文件大小与 splat 数量(评估剪枝目标 30–80 万的距离)
5. `semantics_metadata` 的尺度信息是否跨帧稳定 → 直接决定 P2 配准方案(锚点对齐 vs ICP)
6. 固定 seed 下,相邻帧(0.5s 间隔)的世界差异程度 → 评估闪烁/boiling 的严重性
7. 10 帧 spz 在 Spark 里逐个加载查看,确认几何质量可用

---

## 7. 风险与注意

- **402 错误** = platform 账户 credits 不足,先确认余额再跑
- 生成失败的帧:manifest 标记 failed,重跑脚本自动重新提交(重新提交会再扣费,注意看 error 原因)
- 预签名 URL 有时效,上传步骤失败就重新 prepare_upload
- asset 下载 URL 也可能有时效,生成完成后尽快下载
- operation 有 `expires_at`,别提交后隔太久才去查
