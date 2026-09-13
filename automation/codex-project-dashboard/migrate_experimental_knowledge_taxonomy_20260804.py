#!/usr/bin/env python3
"""Separate reusable instrument knowledge from sequencing and CAAA project work."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

from memory_log_compat import insert_memory_log_entry


VAULT = Path("/Users/shiba/Documents/Obsidian/Silicon Cortex/Codex Memory")
WIKI = VAULT / "wiki"
REGISTRY = WIKI / "project-dashboard-case-registry.json"

SEQUENCING = "project-sequencing"
CAAA = "project-caaa"
INSTRUMENT = "instrument_methodology"

MOVE_TO_INSTRUMENT = {
    "abstract-be0d745278d0bd79da6b",
    "abstract-6811c4b3031f7e892047",
    "abstract-726d0fc7792e4c930d46",
}

EXAM_POINTS = [
    ("2026-06", "质谱原理", "TOF依据离子飞行时间进行质量分析；MRT通过多次反射延长有效飞行路径，提升离子到达时间差的可分辨性。"),
    ("2026-07", "分辨率", "质谱分辨本领通常写作 R=m/Δm，Δm必须声明峰宽口径（常用FWHM）；R本身无量纲，不能用显示的小数位数代替。"),
    ("2026-07", "分辨率", "比较两台高分辨质谱时，应在可比m/z、校准状态、采集模式和处理条件下比较峰宽与分辨本领，不能只看峰形截图。"),
    ("2026-06", "仪器状态", "判断真空是否正常必须对应具体真空区、传感器读数、抽气阶段和仪器状态；单凭界面颜色或指针位置不能下结论。"),
    ("2026-06", "安装验证", "安装检查、校准、IQ和OQ是不同层级的活动；是否完成应以厂商文件、现场配置和签署记录为准，不能因完成校准就推定全部确认完成。"),
    ("2026-06", "液路系统", "WASH、样品流路、CALIBRANT和LOCKMASS承担不同功能：清洗、样品输送、质量校准与运行中质量参照不能混为同一路液体用途。"),
    ("2026-07", "进样系统", "自动进样器最大进样量受样品环、计量装置、硬件配置和方法上限共同约束，应从实际配置与对应手册确认。"),
    ("2026-07", "采集模式", "MSe以低能/高能通道获得数据非依赖碎裂信息，targeted MS/MS先隔离特定前体再采集产物离子；两者的片段归属确定性不同。"),
    ("2026-07", "采集模式", "SIM监测选定质荷比，MRM监测前体离子到产物离子的跃迁；MRM比仅监测单个m/z多一层结构选择性。"),
    ("2026-07", "定量工作流", "三重四极杆方法通常先用Scan或SIM确认前体，再用Product Ion寻找候选碎片，随后优化碰撞条件、建立MRM跃迁并验证响应和峰形。"),
    ("2026-07", "参数边界", "离子源/接口电压与碰撞池能量作用位置不同；跨厂商比较Cone、Fragmentor或CE时应先确认物理位置和功能，不能只按名称或数值互换。"),
    ("2026-07", "数据处理", "TIC表示随时间汇总的总离子信号，色谱峰检测是在时间维度识别峰，deconvolution用于从重叠或多电荷信号中还原组分；三者属于不同处理层。"),
    ("2026-07", "质量口径", "monoisotopic mass、average mass、neutral mass与m/z不是同一口径；软件命中异常时应先核对质量定义、电荷、同位素模型和容差。"),
    ("2026-07", "同位素", "M+1常来自同位素分布，不应仅凭其响应更高就视为新的目标组分；需要结合理论同位素、质量差、电荷和软件分配规则判断。"),
    ("2026-07", "软件建模", "在序列软件中，普通component、修饰和自定义残基承担的语义不同；建模层级错误会影响候选质量、碎片生成和匹配解释。"),
    ("2026-07", "软件边界", "仪器软件的模块、功能边界和操作路径依赖软件版本、许可与数据类型；形成操作结论前必须记录版本并核对对应文档。"),
    ("2026-07", "液相排障", "旁路或反冲时的压力变化只有结合阀切换后的真实流路才能定位堵点；压力下降本身不能直接证明针、柱或某一部件堵塞。"),
    ("2026-06", "色谱排障", "基线噪声或分离度变化应同时核对流动相、检测波长、压力、空白、旁路和色谱柱状态，不能从一次冲洗后的现象单点归因。"),
]

QA_BY_SUMMARY = {
    "区分 Full scan、SIM、MS/MS、Product Ion 与 MRM 的测量对象和证据用途。": (
        "Full scan、SIM、MS/MS、Product Ion 和 MRM 分别测量什么？",
        "Full scan 在设定质量范围内连续采集离子，用于观察整体谱图；SIM 只监测选定 m/z，提高目标离子的灵敏度，但不提供碎片关系。MS/MS 先选择前体再使其碎裂并采集产物离子；Product Ion 是三重四极杆中固定前体、扫描产物离子的 MS/MS 方式；MRM 则固定前体—产物离子对，用跃迁进行高选择性监测。",
    ),
    "跨平台验证应先区分采集对象、子离子选择和碰撞过程，再判断功能相似、实现等价与数值可迁移；参数名相近不代表可直接照搬。": (
        "为什么不同厂商仪器的同名或近似参数不能直接照搬？",
        "不同平台的离子光学结构、参数作用位置、控制算法和标定尺度可能不同。迁移方法时应依次确认测量对象是否相同、功能是否相似、物理实现是否等价，最后才讨论数值能否迁移；可靠做法是以响应、碎裂、稳定性和信噪比重新验证，而不是复制电压或能量数值。",
    ),
    "数据解释需区分原始信号、峰、组分和计算结果，避免跨处理层混用口径。": (
        "原始信号、峰、组分和软件计算结果有什么区别？",
        "原始信号是检测器记录的数据；峰是经过基线、平滑或峰识别后形成的信号特征；组分是软件把同位素、加合物或不同电荷态归并后的化学实体假设；计算结果则包括去卷积质量、匹配分数或定量值。后一级依赖前一级和处理参数，因此软件给出的组分或命中不能反过来当作未经处理的原始证据。",
    ),
    "比较仪器能力前应固定测量定义、硬件配置、软件版本和处理条件。": (
        "怎样才算公平地比较两台分析仪器的能力？",
        "应先定义比较指标，例如分辨本领、质量准确度、灵敏度、动态范围或扫描速度，并统一样品、m/z 区间、采集模式和评价口径。还要记录硬件配置、校准状态、软件版本及数据处理条件；否则差异可能来自配置或算法，而不是仪器平台本身。",
    ),
    "样品前处理与耗材影响应通过空白、回收、重复性和材料对照验证，不从单次现象直接归因。": (
        "如何判断滤膜、样品瓶或其他耗材是否影响分析结果？",
        "至少要设置过程空白、未处理对照、不同材料对照和重复样，并比较回收率、峰面积、杂峰、峰形及重复性。若条件允许，还应区分吸附、析出、浸出物和溶剂效应。单次峰形改善或响应变化只能提示关联，不能单独证明某种材料机制。",
    ),
    "提高碰撞能量不能补偿结构表示或软件建模错误；应先核对表示与处理层，再调整采集参数。": (
        "为什么提高碰撞能量不能解决所有碎片不足或匹配失败？",
        "碰撞能量只改变离子获得的内能和碎裂程度，不能修正错误的分子式、残基定义、修饰位置、电荷状态或软件组件类型。若结构表示或理论碎片模型错误，提高能量可能只会产生更多无法解释的碎片或过度碎裂；应先核对建模与处理层，再优化采集参数。",
    ),
    "TOF依据离子飞行时间进行质量分析；MRT通过多次反射延长有效飞行路径，提升离子到达时间差的可分辨性。": (
        "TOF 和 MRT 的基本原理有什么区别？",
        "TOF 让离子在电场加速后进入飞行区，并根据到达检测器的时间差推算 m/z；在理想情况下，较轻或 m/z 较低的离子更早到达。MRT 属于多反射飞行时间技术，通过静电镜让离子多次往返，增加有效飞行距离和时间展开，从而更容易分开到达时间非常接近的离子。",
    ),
    "质谱分辨本领通常写作 R=m/Δm，Δm必须声明峰宽口径（常用FWHM）；R本身无量纲，不能用显示的小数位数代替。": (
        "质谱分辨本领 R=m/Δm 应该怎样理解？",
        "m 是所评价峰的质量位置，Δm 是按约定口径测得的峰宽或两个峰的最小可分质量差，常见口径是半峰全宽 FWHM。m 与 Δm 使用相同单位，所以 R 无量纲。软件显示多少位小数只是显示精度，不能代表仪器真正把相邻离子峰分开的能力。",
    ),
    "比较两台高分辨质谱时，应在可比m/z、校准状态、采集模式和处理条件下比较峰宽与分辨本领，不能只看峰形截图。": (
        "比较两台高分辨质谱的分辨能力时要控制哪些条件？",
        "应选择相同或相近 m/z 的标准峰，统一分辨率定义和峰宽口径，并记录校准状态、采集模式、扫描速度、离子强度和处理算法。峰在截图上看起来更窄，可能受坐标缩放、平滑和采样点影响；只有在可比条件下计算的分辨本领才有意义。",
    ),
    "判断真空是否正常必须对应具体真空区、传感器读数、抽气阶段和仪器状态；单凭界面颜色或指针位置不能下结论。": (
        "怎样判断质谱仪的真空状态是否正常？",
        "先确认读数属于前级、分析器或其他哪一个真空区，以及使用的传感器类型和单位，再结合抽气时长、涡轮泵状态、仪器所处阶段与厂商阈值判断。界面颜色可能只是状态规则或阶段提示；缺少具体区域和读数时，不能仅凭红区或指针位置判断泄漏或故障。",
    ),
    "安装检查、校准、IQ和OQ是不同层级的活动；是否完成应以厂商文件、现场配置和签署记录为准，不能因完成校准就推定全部确认完成。": (
        "仪器安装、校准、IQ 和 OQ 有什么区别？",
        "安装是完成硬件、环境和连接配置；校准是建立仪器响应与已知参考之间的关系。IQ 用文件化证据确认设备及组件按要求安装，OQ 则确认设备在规定操作范围内能够按预期运行。各厂商流程可能不同，因此完成抽真空或质量校准不等于 IQ/OQ 已全部完成。",
    ),
    "WASH、样品流路、CALIBRANT和LOCKMASS承担不同功能：清洗、样品输送、质量校准与运行中质量参照不能混为同一路液体用途。": (
        "WASH、样品流路、CALIBRANT 和 LOCKMASS 分别有什么作用？",
        "WASH 用于清洗探针或流路、减少残留；样品流路负责把待测样品送入离子源。CALIBRANT 是用于建立质量轴校准关系的已知参考物；LOCKMASS 则在采集过程中提供持续或周期性的参考信号，用于修正质量漂移。具体瓶位和流路以实际型号配置为准。",
    ),
    "自动进样器最大进样量受样品环、计量装置、硬件配置和方法上限共同约束，应从实际配置与对应手册确认。": (
        "自动进样器的最大进样量由什么决定？",
        "上限由进样模式、样品环或计量泵/注射器容量、针与流路设计以及已安装硬件共同决定，软件方法还可能设置更低的允许值。不能只看方法输入框的最大数字；应读取模块配置、部件规格和对应版本手册，并考虑大体积进样对峰形和溶剂效应的影响。",
    ),
    "MSe以低能/高能通道获得数据非依赖碎裂信息，targeted MS/MS先隔离特定前体再采集产物离子；两者的片段归属确定性不同。": (
        "MSe 与 targeted MS/MS 的核心区别是什么？",
        "MSe 通常在低能和高能采集间切换，高能通道中的碎片来自同一时间窗口内进入系统的多种前体，因此需要借助保留时间、峰形或算法进行归属。targeted MS/MS 先选择特定前体，再碰撞并采集其产物离子，碎片与前体的关系通常更直接，但覆盖范围取决于预先选择的目标。",
    ),
    "SIM监测选定质荷比，MRM监测前体离子到产物离子的跃迁；MRM比仅监测单个m/z多一层结构选择性。": (
        "SIM 和 MRM 为什么具有不同的选择性？",
        "SIM 只要求离子落在设定的 m/z 窗口内，因此同质量干扰物也可能产生响应。MRM 先由第一级质量分析器选择前体，再经碰撞产生碎片，最后监测指定产物离子，相当于同时限定前体和碎片关系，所以通常具有更高选择性；但跃迁仍需用标准、离子比和色谱行为验证。",
    ),
    "三重四极杆方法通常先用Scan或SIM确认前体，再用Product Ion寻找候选碎片，随后优化碰撞条件、建立MRM跃迁并验证响应和峰形。": (
        "三重四极杆从寻找母离子到建立 MRM 方法的一般流程是什么？",
        "先用 Scan 或 SIM 确认可稳定检测的前体离子和电荷形式；再固定该前体做 Product Ion 扫描，寻找强度、特异性和稳定性合适的碎片。随后优化源参数与碰撞条件，建立一条定量跃迁和必要的定性跃迁，最后检查保留时间、离子比、峰形、响应、重复性和基质干扰。",
    ),
    "离子源/接口电压与碰撞池能量作用位置不同；跨厂商比较Cone、Fragmentor或CE时应先确认物理位置和功能，不能只按名称或数值互换。": (
        "离子源或接口电压与碰撞能量有什么本质区别？",
        "源区或接口电压主要影响离子从大气压区域进入真空系统时的传输、去簇和源内碎裂；碰撞能量则作用于已选离子在碰撞区中的活化与碎裂。两者作用位置和目标不同。Cone、Fragmentor 等名称只能做功能近似比较，必须结合具体平台的离子光学结构确认，不能与 CE 直接等同。",
    ),
    "TIC表示随时间汇总的总离子信号，色谱峰检测是在时间维度识别峰，deconvolution用于从重叠或多电荷信号中还原组分；三者属于不同处理层。": (
        "TIC、色谱峰检测和 deconvolution 分别解决什么问题？",
        "TIC 把每个扫描时刻的离子强度汇总成随时间变化的曲线，用来观察整体洗脱行为。峰检测在色谱时间维度寻找起点、峰顶和终点；deconvolution 则在质谱数据中把同位素、加合物或多电荷包络归并，估计中性组分质量。它们的输入、算法和结论层级不同。",
    ),
    "monoisotopic mass、average mass、neutral mass与m/z不是同一口径；软件命中异常时应先核对质量定义、电荷、同位素模型和容差。": (
        "monoisotopic mass、average mass、neutral mass 和 m/z 有什么区别？",
        "monoisotopic mass 使用各元素最轻常见同位素组成的精确质量；average mass 按天然同位素丰度计算加权平均质量。neutral mass 指不带净电荷分子的质量，而 m/z 是离子质量与电荷数之比，还会受到质子化、去质子化或加合物影响。软件匹配前必须确认所用质量口径与电荷模型一致。",
    ),
    "M+1常来自同位素分布，不应仅凭其响应更高就视为新的目标组分；需要结合理论同位素、质量差、电荷和软件分配规则判断。": (
        "质谱中的 M+1 峰通常表示什么，为什么不能直接当成新组分？",
        "M+1 往往来自含一个较重同位素的同位素峰，例如 13C 对同位素包络的贡献；对多电荷离子，峰间距还会按电荷数缩小。分子较大时 M+1 甚至可能高于单同位素峰。判断时应结合理论同位素分布、峰间距、电荷态、保留时间和软件的同位素归并结果。",
    ),
    "在序列软件中，普通component、修饰和自定义残基承担的语义不同；建模层级错误会影响候选质量、碎片生成和匹配解释。": (
        "为什么序列软件中 component、修饰和自定义残基不能混用？",
        "component 往往表示一个整体分析对象，修饰表示在既有残基或结构上的质量/组成变化，自定义残基则进入序列语法并参与理论碎片生成。若把残基当成整体 component，或把结构变化放错层级，软件计算的前体质量、断裂位置和候选碎片都会改变，因此可能出现母离子能匹配而序列碎片无法正确解释的情况。",
    ),
    "仪器软件的模块、功能边界和操作路径依赖软件版本、许可与数据类型；形成操作结论前必须记录版本并核对对应文档。": (
        "为什么仪器软件的操作步骤必须注明版本和模块？",
        "同一产品名下，不同版本可能更改菜单、算法、数据格式和功能命名；许可或已安装模块也会决定某些入口是否存在。采集数据类型与处理工作流不匹配时，界面即使相似也可能不能执行同一分析。因此可复用步骤至少要记录软件版本、模块/许可、数据类型和对应文档版本。",
    ),
    "旁路或反冲时的压力变化只有结合阀切换后的真实流路才能定位堵点；压力下降本身不能直接证明针、柱或某一部件堵塞。": (
        "为什么切换旁路后压力下降不能直接证明某个部件堵塞？",
        "阀切换会改变哪些部件处于泵的下游；压力下降只能说明被绕开的那段流路贡献了阻力。若不知道具体端口连接、阀位和废液路径，就无法区分色谱柱、针座、毛细管、过滤片或其他部件。排障应根据流路图逐段隔离，并结合流量、泄漏和重复切换验证。",
    ),
    "基线噪声或分离度变化应同时核对流动相、检测波长、压力、空白、旁路和色谱柱状态，不能从一次冲洗后的现象单点归因。": (
        "出现色谱基线噪声或分离度下降时应怎样系统排查？",
        "先用空白和旁路区分检测器/流动相问题与色谱柱问题，再检查流动相配制、脱气、污染、检测波长、温度、流量和压力是否稳定。随后比较新旧柱、接头与系统体积，并观察冲洗前后的可重复变化。一次冲洗后改善只能说明条件变化与现象相关，不能单独确认根因。",
    ),
}

INSTRUMENT_TYPES_BY_QUESTION = {
    "Full scan、SIM、MS/MS、Product Ion 和 MRM 分别测量什么？": ["质谱", "三重四极杆质谱"],
    "为什么不同厂商仪器的同名或近似参数不能直接照搬？": ["通用分析仪器"],
    "原始信号、峰、组分和软件计算结果有什么区别？": ["通用分析仪器", "仪器软件"],
    "怎样才算公平地比较两台分析仪器的能力？": ["通用分析仪器"],
    "如何判断滤膜、样品瓶或其他耗材是否影响分析结果？": ["通用分析仪器"],
    "为什么提高碰撞能量不能解决所有碎片不足或匹配失败？": ["质谱", "仪器软件"],
    "TOF 和 MRT 的基本原理有什么区别？": ["质谱", "飞行时间质谱"],
    "质谱分辨本领 R=m/Δm 应该怎样理解？": ["质谱"],
    "比较两台高分辨质谱的分辨能力时要控制哪些条件？": ["质谱", "高分辨质谱"],
    "怎样判断质谱仪的真空状态是否正常？": ["质谱"],
    "仪器安装、校准、IQ 和 OQ 有什么区别？": ["通用分析仪器"],
    "WASH、样品流路、CALIBRANT 和 LOCKMASS 分别有什么作用？": ["质谱"],
    "自动进样器的最大进样量由什么决定？": ["液相色谱", "自动进样器"],
    "MSe 与 targeted MS/MS 的核心区别是什么？": ["质谱"],
    "SIM 和 MRM 为什么具有不同的选择性？": ["质谱", "三重四极杆质谱"],
    "三重四极杆从寻找母离子到建立 MRM 方法的一般流程是什么？": ["质谱", "三重四极杆质谱"],
    "离子源或接口电压与碰撞能量有什么本质区别？": ["质谱"],
    "TIC、色谱峰检测和 deconvolution 分别解决什么问题？": ["质谱", "仪器软件"],
    "monoisotopic mass、average mass、neutral mass 和 m/z 有什么区别？": ["质谱", "仪器软件"],
    "质谱中的 M+1 峰通常表示什么，为什么不能直接当成新组分？": ["质谱"],
    "为什么序列软件中 component、修饰和自定义残基不能混用？": ["质谱", "仪器软件"],
    "为什么仪器软件的操作步骤必须注明版本和模块？": ["仪器软件"],
    "为什么切换旁路后压力下降不能直接证明某个部件堵塞？": ["液相色谱"],
    "出现色谱基线噪声或分离度下降时应怎样系统排查？": ["液相色谱"],
}


def now_iso() -> str:
    return datetime.now(ZoneInfo("Asia/Shanghai")).isoformat()


def stable_id(*parts: str, prefix: str) -> str:
    digest = hashlib.sha256("\n".join(parts).encode("utf-8")).hexdigest()[:20]
    return f"{prefix}{digest}"


def project(registry: dict, project_id: str) -> dict:
    for item in registry.get("cases", []):
        if item.get("case_id") == project_id:
            return item
    raise RuntimeError(f"missing experimental project: {project_id}")


def migrate_registry(registry: dict, timestamp: str) -> dict:
    sequencing = project(registry, SEQUENCING)
    caaa = project(registry, CAAA)

    registry["capability_domains"] = [
        {
            "capability_domain_id": INSTRUMENT,
            "title": "仪器知识与应用方法论",
            "summary": "跨多项目沉淀可独立复用、可作为考点的仪器知识、结论与适用边界；每道考点标注适用的仪器类型，但不计作解序或 CAAA 进展。",
            "tag_label": "仪器类型",
        }
    ]

    for item in (sequencing, caaa):
        item.pop("capability_domain_ids", None)
        item["related_capability_domain_ids"] = [INSTRUMENT]
        item["updated_at"] = timestamp

    sequencing["current_summary"] = (
        "已围绕结构表示、碎片证据、肽图方法和序列确认边界形成持续工作方向；"
        "通用仪器知识已单独沉淀，不计作解序进展。"
    )
    sequencing["value_items"] = [
        {
            **item,
            "detail": (
                "结构表示与可解释对象建模应先于序列判断；表示层问题应先解决，"
                "再评估碎片证据。"
            ) if item.get("value_id") == "value-edb3d78a8bab1c2695d6" else item.get("detail"),
        }
        for item in sequencing.get("value_items", [])
    ]

    caaa["current_summary"] = (
        "已围绕水解、衍生化、构型判定与诊断证据建立持续工作方向；"
        "通用采集模式和跨平台知识已单独沉淀。"
    )
    caaa["next_step"] = (
        "水解与衍生化造成的质量变化和异常信号解释仍需保持结构先行。\n"
        "构型判断所需的诊断证据、对照和适用边界仍需继续积累。"
    )
    caaa["value_items"] = [
        item for item in caaa.get("value_items", [])
        if item.get("value_id") != "value-d6a24cfedbf91ea03704"
    ]

    events = registry.setdefault("events", [])
    for event in events:
        event_id = str(event.get("event_id") or "")
        if event_id in MOVE_TO_INSTRUMENT:
            former_project = str(event.get("case_id") or "")
            event["state"] = "capability_item"
            event["capability_domain_id"] = INSTRUMENT
            event["related_project_ids"] = [former_project] if former_project else []
            event["knowledge_kind"] = "instrument_methodology"
            event["progress_node"] = False
            event["routing_reason"] = "instrument_knowledge_extracted_from_experimental_project"
            event.pop("case_id", None)
            event.pop("capability_domain_ids", None)
        elif event.get("case_id") in {SEQUENCING, CAAA}:
            event.pop("capability_domain_ids", None)

        if event.get("capability_domain_id") == "peptide_mapping":
            event["state"] = "linked"
            event["case_id"] = SEQUENCING
            event["knowledge_kind"] = "sequencing_method"
            event["progress_node"] = False
            event["routing_reason"] = "peptide_mapping_belongs_to_sequencing"
            event.pop("capability_domain_id", None)
            event.pop("branch_ids", None)

        if event_id == "capability-d3e733ab2777a9e6a46d":
            event["detail"] = (
                "跨平台验证应先区分采集对象、子离子选择和碰撞过程，再判断功能相似、"
                "实现等价与数值可迁移；参数名相近不代表可直接照搬。"
            )
            event["related_project_ids"] = [SEQUENCING, CAAA]

    extracted_rewrites = {
        "abstract-be0d745278d0bd79da6b": ("跨平台方法", "跨平台迁移应先对齐测量对象和信号链，再对齐功能模块，最后才比较参数数值。"),
        "abstract-6811c4b3031f7e892047": ("定量工作流", "从未知信号到定量跃迁应分阶段完成前体确认、产物离子筛选、碰撞条件优化和最终方法验证。"),
        "abstract-726d0fc7792e4c930d46": ("软件边界", "采集模式与验证步骤的对应关系必须结合仪器型号、软件版本和厂商文档确认，不能只依赖模式名称。"),
    }
    for event in events:
        if event.get("event_id") in extracted_rewrites:
            event["topic"], event["detail"] = extracted_rewrites[str(event["event_id"])]
            event["state"] = "retired"
            event["knowledge_kind"] = "source_signal"
            event["knowledge_status"] = "superseded"
            event["retired_reason"] = "已合并为更明确、可独立作答且去重后的知识考点。"

    existing_topics = {
        "capability-e119d6cd7fd6a4542189": "采集模式",
        "capability-d3e733ab2777a9e6a46d": "跨平台方法",
        "capability-6d3c56cc2c5b0addf31f": "数据处理",
        "capability-b60048377b011596b42d": "仪器比较",
        "capability-c39feceec7990bf88668": "方法学验证",
        "capability-2f6e5b4ea215fddc449a": "参数边界",
    }
    for event in events:
        if event.get("event_id") in existing_topics:
            event["topic"] = existing_topics[str(event["event_id"])]
            event["knowledge_kind"] = "exam_point"
            event["knowledge_status"] = "reusable"

    energy_detail = (
        "提高碰撞能量不能补偿结构表示或软件建模错误；应先核对表示与处理层，"
        "再调整采集参数。"
    )
    energy_id = stable_id(INSTRUMENT, "2026-07", energy_detail, prefix="capability-")
    if not any(event.get("event_id") == energy_id for event in events):
        events.append({
            "event_id": energy_id,
            "period": "2026-07",
            "title": "能力方法沉淀",
            "detail": energy_detail,
            "source": "compliance_abstracted_from_prior_memory",
            "source_kind": "compliance_abstracted",
            "source_status": "abstracted",
            "coverage": "bounded_partial",
            "evidence_type": "bounded_synthesis",
            "state": "capability_item",
            "capability_domain_id": INSTRUMENT,
            "related_project_ids": [SEQUENCING],
            "knowledge_kind": "instrument_methodology",
            "topic": "参数边界",
            "knowledge_status": "reusable",
            "progress_node": False,
            "privacy_mode": "non_reconstructable",
            "created_at": timestamp,
            "verification_refs": [],
        })

    existing_details = {str(event.get("summary") or event.get("detail") or "") for event in events}
    for period, topic, detail in EXAM_POINTS:
        if detail in existing_details:
            continue
        events.append({
            "event_id": stable_id(INSTRUMENT, period, topic, detail, prefix="instrument-knowledge-"),
            "period": period,
            "title": "知识与结论",
            "detail": detail,
            "topic": topic,
            "source": "compliance_abstracted_from_daily_reports",
            "source_kind": "compliance_abstracted",
            "source_status": "abstracted",
            "coverage": "bounded_partial",
            "evidence_type": "bounded_synthesis",
            "evidence_boundary": "从日报中归纳的通用知识；具体型号、软件版本与数值仍以对应厂商文档为准。",
            "state": "capability_item",
            "capability_domain_id": INSTRUMENT,
            "related_project_ids": [],
            "knowledge_kind": "exam_point",
            "knowledge_status": "reusable",
            "privacy_mode": "non_reconstructable",
            "progress_node": False,
            "created_at": timestamp,
            "verification_refs": [],
        })
        existing_details.add(detail)

    for event in events:
        if event.get("state") != "capability_item" or event.get("capability_domain_id") != INSTRUMENT:
            continue
        summary = str(event.get("summary") or event.get("detail") or "").strip()
        qa = QA_BY_SUMMARY.get(summary)
        if not qa:
            continue
        question, answer = qa
        event["summary"] = summary
        event["question"] = question
        event["answer"] = answer
        event["detail"] = answer
        event["instrument_types"] = INSTRUMENT_TYPES_BY_QUESTION[question]
        event["knowledge_kind"] = "exam_qa"
        event["knowledge_status"] = "reusable"

    audit = registry.setdefault("migration_audit", [])
    audit_key = "experimental_knowledge_taxonomy_correction"
    if not any(item.get("kind") == audit_key for item in audit):
        audit.append({
            "at": timestamp,
            "kind": audit_key,
            "authority": "user_confirmed",
            "summary": (
                "从解序和 CAAA 中抽出通用仪器能力知识；肽图归入解序；"
                "项目只保留目标相关工作，能力域不计项目进展。"
            ),
        })

    registry["updated_at"] = timestamp
    return registry


def insert_after_heading(text: str, heading: str, entry: str) -> str:
    if entry.splitlines()[0] in text:
        return text
    marker = heading + "\n\n"
    if marker not in text:
        raise RuntimeError(f"heading not found: {heading}")
    return text.replace(marker, marker + entry, 1)


def main() -> int:
    timestamp = now_iso()
    registry = json.loads(REGISTRY.read_text(encoding="utf-8"))
    registry = migrate_registry(registry, timestamp)
    REGISTRY.write_text(json.dumps(registry, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    decisions = WIKI / "decisions.md"
    decision_entry = (
        "## [2026-08-04] 实验项目与仪器能力知识分离\n\n"
        "- 决策：分类单位是知识点或工作片段，不是整场会话。通用仪器原理、采集模式、软件处理层、硬件边界和跨平台方法统一进入“仪器知识与应用方法论”，不计作解序或 CAAA 进展。\n"
        "- 解序只保留以序列确定或确认为目标的结构表示、肽图方法、碎片证据、覆盖边界和序列解释；CAAA 只保留水解、衍生化、构型判定、诊断证据和针对该目标的验证。\n"
        "- 混合会话按信息片段拆分：能力知识只保存一次，并可关联解序或 CAAA；只有目标相关的方法推进、证据变化或判断收敛才进入项目。\n\n"
    )
    decisions.write_text(
        insert_after_heading(decisions.read_text(encoding="utf-8"), "# 决策", decision_entry),
        encoding="utf-8",
    )
    decisions_text = decisions.read_text(encoding="utf-8")
    exam_heading = "## [2026-08-04] 仪器能力域保留可考核知识点"
    if exam_heading not in decisions_text:
        exam_entry = (
            f"{exam_heading}\n\n"
            "- 决策：每日日报中形成的通用、可复用、能够独立表述为考点的仪器知识或结论，都沉淀到“仪器知识与应用方法论”，不要求它已经推动某个项目。\n"
            "- 质量门槛：只收录明确知识、概念区别、判断框架和适用边界；单纯问题、猜测、一次性现象、未经支持的因果和具体项目参数不作为知识结论。具体型号、软件版本与数值仍需厂商文档复核。\n"
            "- 未来日报必须生成机器可读的 `Instrument Knowledge Candidates`；能力知识只保存一次，可关联解序或 CAAA，但不计作项目进展。\n\n"
        )
        decisions.write_text(insert_after_heading(decisions_text, "# 决策", exam_entry), encoding="utf-8")
    decisions_text = decisions.read_text(encoding="utf-8")
    qa_heading = "## [2026-08-04] 仪器考点必须附展开答案"
    if qa_heading not in decisions_text:
        qa_entry = (
            f"{qa_heading}\n\n"
            "- 决策：能力域中的考点必须同时包含明确问题和可独立复习的展开答案；短总结只作索引，不作为知识正文。\n"
            "- 答案至少解释概念、关键区别或判断逻辑，并保留适用边界；无答案的问题、工作进度描述和只有结论没有解释的口号式条目不得进入能力域。\n"
            "- H5 使用可展开问答卡展示；未来日报候选缺少 `question` 或实质性 `answer` 时，解析器直接拒绝摄取。\n\n"
        )
        decisions.write_text(insert_after_heading(decisions_text, "# 决策", qa_entry), encoding="utf-8")
    decisions_text = decisions.read_text(encoding="utf-8")
    type_heading = "## [2026-08-04] 仪器考点必须标注仪器类型"
    if type_heading not in decisions_text:
        type_entry = (
            f"{type_heading}\n\n"
            "- 决策：“仪器知识与应用方法论”是跨多项目可复用、可考核的仪器知识库；每道考点必须使用 `instrument_types` 标注适用仪器类型。\n"
            "- 标签允许多选，采用规范化类型名称；通用知识标为“通用分析仪器”，硬件与软件共同决定适用边界时可同时标注对应硬件类型与“仪器软件”。\n"
            "- 仪器类型只用于知识检索和筛选，不改变解序、CAAA 的项目归属，也不计作项目进展。\n\n"
        )
        decisions.write_text(insert_after_heading(decisions_text, "# 决策", type_entry), encoding="utf-8")
    decisions_text = decisions.read_text(encoding="utf-8")
    scoped_tag_heading = "## [2026-08-04] 能力标签从属于各自能力域"
    if scoped_tag_heading not in decisions_text:
        scoped_tag_entry = (
            f"{scoped_tag_heading}\n\n"
            "- 决策：标签不是“能力域”页面的全局分类，而是每个具体能力域内部的知识组织维度。\n"
            "- 当前“仪器知识与应用方法论”内部使用“仪器类型”标签；未来新增能力域时，由新能力域自行定义不同的 `tag_label` 与标签集合，彼此不共用筛选状态。\n\n"
        )
        decisions.write_text(insert_after_heading(decisions_text, "# 决策", scoped_tag_entry), encoding="utf-8")
    decisions_text = decisions.read_text(encoding="utf-8")
    governance_heading = "## [2026-08-04] 合规抽象不是知识标签"
    if governance_heading not in decisions_text:
        governance_entry = (
            f"{governance_heading}\n\n"
            "- 纠正：“合规抽象”描述内容如何脱敏和保存，属于证据治理元数据，不是能力域、知识主题或检索标签。\n"
            "- H5 不把“合规抽象”渲染成用户可见徽标；该状态仍可在底层证据字段中保留，用于约束不可还原具体项目。\n\n"
        )
        decisions.write_text(insert_after_heading(decisions_text, "# 决策", governance_entry), encoding="utf-8")

    agents_path = VAULT / "AGENTS.memory.md"
    agents_text = agents_path.read_text(encoding="utf-8")
    old_policy = (
        "- 实验工作只保存长期“需求分支”、合规抽象进展、知识缺口、横向能力方法和可复用价值；"
        "当前需求分支为“解序”和“CAAA”，“仪器知识与应用方法论”是可横向关联多个分支的能力域。"
    )
    new_policy = (
        "- 实验工作只保存合规抽象项目进展、知识缺口、横向能力方法和可复用价值；"
        "当前实验工作项目为“解序”和“CAAA”，“仪器知识与应用方法论”是唯一横向能力域，肽图归入解序。"
    )
    if old_policy in agents_text:
        agents_text = agents_text.replace(old_policy, new_policy, 1)
    classification_policy = (
        "- 实验日报按知识点或工作片段分类，不按整场会话分类：通用仪器原理、采集模式、软件处理层、硬件边界、参数边界和跨平台方法进入“仪器知识与应用方法论”，可关联项目但不计项目进展；以序列确认为目标的结构表示、肽图、碎片证据、覆盖边界和序列解释进入“解序”；水解、衍生化、构型判定、诊断证据和目标相关验证进入“CAAA”。混合会话必须拆分，能力知识只保存一次。\n"
    )
    if classification_policy not in agents_text:
        agents_text = agents_text.replace(new_policy + "\n", new_policy + "\n" + classification_policy, 1)
    exam_policy = (
        "- 每份新日报都必须从所有相关会话中提取可复用的仪器知识，并生成机器可读的 `Instrument Knowledge Candidates`。每条必须同时含明确的 `question`、可独立复习的展开 `answer`，以及非空规范化 `instrument_types` 仪器类型标签；可选 `summary` 只用于导航，不能代替答案。无答案的问题、无仪器类型的条目、猜测、单次现象、具体项目参数和未经支持的因果不进入能力域。\n"
    )
    previous_exam_policy = (
        "- 每份新日报都必须从所有相关会话中提取可复用的仪器知识，并生成机器可读的 `Instrument Knowledge Candidates`。每条必须同时含明确的 `question` 和可独立复习的展开 `answer`；可选 `summary` 只用于导航，不能代替答案。无答案的问题、猜测、单次现象、具体项目参数和未经支持的因果不进入能力域。\n"
    )
    old_exam_policy = (
        "- 每份新日报都必须从所有相关会话中提取可复用、可独立表述为考点的仪器知识或结论，并生成机器可读的 `Instrument Knowledge Candidates`。只收录明确知识、概念区别、判断框架和适用边界；问题、猜测、单次现象、具体项目参数和未经支持的因果不进入能力域。\n"
    )
    if previous_exam_policy in agents_text:
        agents_text = agents_text.replace(previous_exam_policy, exam_policy, 1)
    elif old_exam_policy in agents_text:
        agents_text = agents_text.replace(old_exam_policy, exam_policy, 1)
    elif exam_policy not in agents_text:
        agents_text = agents_text.replace(classification_policy, classification_policy + exam_policy, 1)
    scoped_tag_policy = (
        "- 能力标签从属于具体能力域，不作为“能力域”页面的全局标签体系。当前“仪器知识与应用方法论”的标签维度为“仪器类型”；未来新增能力域时可定义不同的标签维度与标签集合。\n"
    )
    if scoped_tag_policy not in agents_text:
        agents_text = agents_text.replace(exam_policy, exam_policy + scoped_tag_policy, 1)
    governance_policy = (
        "- `compliance_abstracted` / “合规抽象”是证据治理与隐私处理状态，不是知识分类标签；可以保留在底层证据字段中，但不得在能力域 H5 中渲染为用户可见标签。\n"
    )
    if governance_policy not in agents_text:
        agents_text = agents_text.replace(scoped_tag_policy, scoped_tag_policy + governance_policy, 1)
    agents_path.write_text(agents_text, encoding="utf-8")

    index_path = WIKI / "index.md"
    index_text = index_path.read_text(encoding="utf-8")
    index_text = index_text.replace(
        "实验工作仍只保存合规抽象需求分支与能力域",
        "实验工作按解序、CAAA 与仪器能力域分离沉淀",
    )
    index_path.write_text(index_text, encoding="utf-8")

    log_heading = "## [2026-08-04] update | 分离实验项目与仪器能力知识"
    entry = (
        f"{log_heading}\n\n"
        "- 从解序与 CAAA 的摘要、开放项、价值和事件中抽出通用仪器知识，统一归入“仪器知识与应用方法论”；能力项保留项目关联但不再计作项目进展。\n"
        "- 肽图知识范围并入解序，不再作为独立能力域；未来日报按知识片段而非整场会话分类。\n\n"
    )
    insert_memory_log_entry(VAULT, entry)

    qa_log_heading = "## [2026-08-04] update | 仪器能力知识升级为考点问答"
    qa_entry = (
        f"{qa_log_heading}\n\n"
        "- 将能力域中的历史知识条目改为“考点问题 + 展开答案 + 证据边界”，短总结只作导航，不再冒充知识正文。\n"
        "- 未来 `Instrument Knowledge Candidates` 强制要求 `question` 与实质性 `answer`；无答案的问题不得进入能力域。\n\n"
    )
    insert_memory_log_entry(VAULT, qa_entry)

    type_log_heading = "## [2026-08-04] update | 仪器考点增加仪器类型标签"
    type_entry = (
        f"{type_log_heading}\n\n"
        "- 为能力域全部历史考点增加规范化 `instrument_types` 标签，并支持一条知识关联多个仪器类型。\n"
        "- 未来日报候选必须提供非空仪器类型标签；H5 同步展示并支持按类型筛选。\n\n"
    )
    insert_memory_log_entry(VAULT, type_entry)

    scoped_tag_log_heading = "## [2026-08-04] update | 能力标签下沉到具体能力域"
    scoped_tag_entry = (
        f"{scoped_tag_log_heading}\n\n"
        "- 将“仪器类型”标签与筛选器下沉到“仪器知识与应用方法论”内部；不再作为能力域页面的全局筛选。\n"
        "- 数据模型允许未来每个新能力域定义自己的标签名称和标签集合。\n\n"
    )
    insert_memory_log_entry(VAULT, scoped_tag_entry)

    governance_log_heading = "## [2026-08-04] update | 移除合规抽象展示标签"
    governance_log_entry = (
        f"{governance_log_heading}\n\n"
        "- “合规抽象”继续作为底层证据治理状态保留，但不再作为 H5 用户可见标签。\n"
        "- 能力域界面只展示知识主题和该能力域定义的检索标签。\n\n"
    )
    insert_memory_log_entry(VAULT, governance_log_entry)

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
