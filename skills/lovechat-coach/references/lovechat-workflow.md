# LoveChat Coach 的程序与档案适配

上游参考中的 ChatLab、全局用户目录和 `scripts/memory_store.py` 描述的是上游原产品；本项目不会自动安装或执行这些工具。使用下列实际适配。

## 桌面使用

完整程序项目地址：<https://github.com/ray404butworks/lovechat-coach>。定位实际项目中的 `pyproject.toml`、`恋聊教练.vbs` 和 `scripts/bootstrap_desktop.py`；不存在时说明尚未安装完整程序，仍可在当前对话做教练分析。

双击 `恋聊教练.vbs`，首次按模型、密钥、双方昵称、聊天导入、人设、双方信息与目标、上传同意的顺序配置。每次只补当前缺少的一项；密钥只在本机遮蔽输入，Windows DPAPI 加密，不在聊天索取或复述。已有设置保留。

“军师方案”可选帮我回复、分析局面、复盘我的表达或陪我演练；填写本轮问题并保存，再在首页粘贴对方来信或生成。回复正文与军师分析分栏显示，生成和复制都不等于已发送。

## 数据来源

支持本人提供的 TXT/JSON 和当前指定私聊画面。导入前核对对象、双方说话人、消息数量和时间范围。没有读取的图片、语音、附件、微信服务器历史仍未知。ChatLab 专用工具只有在宿主实际提供时才使用，不声称本程序已接通。

## 精简档案

与原始聊天仓库、旧的已确认记忆分开。首次明确启用后才可保存；模型只提出事件或假设候选，本人核对后保存。稳定本人资料只能来自本人明确陈述；对象和双方共识只能来自本人明确陈述或转述；聊天摘录不能自动升级成人格、爱意和承诺。

五类：本人、对象、关系、关键事件、暂定假设。单条最多200字，总量200条；每对象事件20条、假设5条，撤销历史20次。可查看、暂停、恢复、撤回、撤销或忘记对象；忘记同时删除该对象的撤销历史，不能靠撤销恢复。

桌面入口在“长期记忆”页；“军师方案”页可核对本轮候选。CLI适配入口是本技能的 `scripts/relationship_memory.py`。传入已核实的完整项目路径，不使用开发者路径：

```text
python scripts/relationship_memory.py --project <实际项目目录> status
python scripts/relationship_memory.py --project <实际项目目录> --confirm enable
python scripts/relationship_memory.py --project <实际项目目录> --contact <准确对象> context
python scripts/relationship_memory.py --project <实际项目目录> --contact <准确对象> undo
```

更新先准备仅含核对内容的本地JSON变更文件，再用 `apply --change-file <文件> --confirm`；暂停用 `pause`，恢复用 `resume`，撤回用 `revoke`，忘记对象用 `forget --contact <对象> --confirm`。操作成功后简短告知变更和撤销方式。失败时明确未保存。没有程序依赖时只保留当前会话上下文，不声称持久记忆。

## 升级兼容

既有双方资料、人设、历史、目标与模型设置沿用；旧技能设置作为表达偏好保留，主流程已切换为军师决策。阶段与邀约修复继续有效，不能把“哈哈”当好感、改期待确认当接受、过期当已见面。
