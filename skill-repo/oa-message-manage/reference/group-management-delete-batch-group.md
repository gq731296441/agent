## 群管理：批量删除群



**触发**：批量解散群、批量删除群、一次解散多个群、批量解散群聊、把这几个群都解散掉 等。命中即执行，勿再问用户。



> ⚠️ **高危操作，不可恢复。执行前必须完成二次确认流程。**



### 核心规则（必守）



1. **脚本**：批量删除群用 `{baseDir}/scripts/groupmanage_delbatchgroup.py`；按条件筛选候选群用 `{baseDir}/scripts/search_group_list.py`；按单个群名称搜索仍可使用 `{baseDir}/scripts/search_group.py`。

2. **业务约束：仅可解散「自己是群主」的群**：用户（调用者）**只能解散自己担任群主的群**。是否在目标列表中满足该条件，必须依据 `search_group_list.py`（或同类查询）返回结果中的群主信息（如 `adminID` 与当前工号一致）等在**脚本外**完成核对与筛选；**脚本内不做群主校验**，亦不因非群主而拦截调用。

3. **单次批量上限**：`--groupids` 单次调用**最多 100 个**群 URI（去重后计数）。超过 100 个须**分批**执行多轮批量删除。

4. **群 URI（`--groupids`）**：

   - 批量脚本只接受多个群 URI，格式为多个 `sip:xxxxxx@gm.zte.com.cn` 以英文逗号分隔。

   - 用户提供符合格式的内容 → 直接使用，**无需搜索**（但仍须遵守上文群主与数量上限规则）。

   - 用户提供的是多个明确群名称 → **可逐个调用** `search_group.py --groupname "群名称"` 查询每个群的 URI。

   - 用户提供的是筛选条件（如群名关键字、群主工号、最后消息时间、是否有正式群名）→ **必须先调用** `search_group_list.py` 查询候选群。

   - 查询结果为 0 个：提示未找到符合条件的群；查询结果 >= 1 个：先向用户展示候选群（群名、群主工号、群 URI 等）并确认目标，**确认调用者为群主后再**将群 URI 传给批量删除脚本。

5. **二次确认（必做）**：

   - 可先以 `--check-only` 调用脚本，仅核对传入的群 URI 列表与数量（脚本不拉取群详情）；向用户展示待解散信息时，**群名、群主、是否本人为群主等须结合前置查询结果**一并说明。

   - 向用户展示待解散群数量及每个目标群的关键信息，明确提示这是**高风险不可恢复操作**，要求用户明确确认。

   - 只有用户明确回复「确认删除」「确定删除」等肯定语义后，才可执行正式删除。

   - 若用户未确认、表述含糊、改变目标群，或长时间未回复 → **停止流程，不得调用删除脚本**。

   - 正式删除时，必须额外传入 `--confirm-groupids`，其值必须与 `--groupids` 完全一致。

6. **用户信息**：脚本自动从环境变量获取，无需手动传入。



### 参数摘要



| 参数 | 必填 | 说明 |

|------|------|------|

| `--groupids` | **必填** | 目标群 URI 列表，英文逗号分隔；**单次最多 100 个**（去重后） |

| `--confirm-groupids` | 正式删除必填 | 二次确认群 URI 列表，必须与 `--groupids` 完全一致；`--check-only` 模式不需要 |

| `--check-only` | 可选 | 仅输出待解散的群 URI 与数量，不调用删除接口（不做群主校验） |



### 调用示例



```bash

# 方式一：已知多个群 URI 时，可先 --check-only 核对数量与 URI 列表

python "{baseDir}/scripts/groupmanage_delbatchgroup.py" --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --check-only



# 方式二：用户明确确认后，正式批量删除群

python "{baseDir}/scripts/groupmanage_delbatchgroup.py" --groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn" --confirm-groupids "sip:p1_demo_a@gm.zte.com.cn,sip:p1_demo_b@gm.zte.com.cn"



# 方式三：用户先按条件筛选候选群（例如：群名包含“开发推动”，且群主为当前用户工号）

python "{baseDir}/scripts/search_group_list.py" --groupname "开发推动" --adminid "10145777"



# 方式四：从查询结果中确认目标群 URI 后，可先 --check-only，再正式删除

python "{baseDir}/scripts/groupmanage_delbatchgroup.py" --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --check-only



python "{baseDir}/scripts/groupmanage_delbatchgroup.py" --groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn" --confirm-groupids "sip:rcs_demo_a@gm.zte.com.cn,sip:rcs_demo_b@gm.zte.com.cn"

```



### 返回格式与展示规则



```text

待解散群数量：2

成功解散群数量：2

失败群数量：0

成功[1] 群URI ：sip:p1_demo_a@gm.zte.com.cn

成功[1] 删除结果：成功

```



- 删除前须向用户展示**待解散群数量**及各群关键信息（优先使用 `search_group_list` 等查询结果中的群名、群主工号等），并二次确认。

- 批量删除完成后，优先展示成功数量、失败数量，以及失败群原因（以接口返回为准）。


