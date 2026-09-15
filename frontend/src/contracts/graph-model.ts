// 合同类型可视化编辑器 —— 图模型与序列化工具。
// 图(graph) 是可视化创作界面；保存时序列化为后端引擎消费的
// partyRoles / inputSchema / effects / conditions 四个 JSON（引擎不改）。

export type GNodeType =
  | "root"
  | "party"
  | "input"
  | "value"
  | "effect"
  | "condition"
  | "list-op"
  | "dict-op"
  | "calc"
  | "if"
  | "foreach"
  | "assign"
  | "compare"
  | "output";

export interface GNode {
  id: string;
  type: GNodeType;
  x: number;
  y: number;
  data: Record<string, any>;
}

export interface GEdge {
  id: string;
  source: string; // 源节点 id
  sourceHandle?: string; // 源输出端口
  target: string; // 目标节点 id
  targetHandle?: string; // 目标输入端口
}

export interface GGraph {
  nodes: GNode[];
  edges: GEdge[];
}

// 节点端口定义：编辑器渲染连线、序列化据此解析引用。
export const NODE_PORTS: Record<GNodeType, { inputs: string[]; outputs: string[] }> = {
  root: { inputs: [], outputs: ["out"] },
  party: { inputs: [], outputs: ["out", "companyName"] },
  input: { inputs: ["parent"], outputs: ["out"] },
  value: {
    inputs: ["entityRef", "multiplyByInput", "key", "value", "routeRef", "fieldParty"],
    outputs: ["out"],
  },
  effect: {
    inputs: ["parent", "party", "value", "value2"],
    outputs: [],
  },
  condition: {
    inputs: ["parent", "value1", "value2"],
    outputs: [],
  },
  // 运算节点（列表/字典/算术）的输入端口由所选 op 动态决定，见 OP_ARG_SPECS / nodeInputs。
  "list-op": { inputs: [], outputs: ["out"] },
  "dict-op": { inputs: [], outputs: ["out"] },
  // 计算节点（算术）：两个数值输入端口 + 一个输出端口。
  calc: { inputs: ["left", "right"], outputs: ["out"] },
  // 控制流节点：parent 表示"隶属于某个控制流"（子节点由此被嵌套）；
  // then/else/body 为输出端口，连线到子效果的 parent 输入。
  if: { inputs: ["cond", "parent"], outputs: ["then", "else"] },
  foreach: { inputs: ["items", "parent"], outputs: ["body"] },
  assign: { inputs: ["value", "parent"], outputs: [] },
  // 布尔比较（条件式）：两个数值输入端口，输出一个布尔值，连到 IF 节点的「条件」端口。
  compare: { inputs: ["left", "right"], outputs: ["out"] },
  // 产业计算图的结果汇点：唯一的输出节点，其「值」输入端口连接的表达式结果 = 该计算字段的存储值。
  output: { inputs: ["value"], outputs: [] },
};

// 各节点类型的标题与配色（供画布渲染）。
export const NODE_META: Record<
  GNodeType,
  { title: string; color: string; inputs: string[]; outputs: string[] }
> = {
  root: { title: "合同", color: "#8e44ad", inputs: [], outputs: ["out"] },
  party: { title: "参与方", color: "#2980b9", inputs: [], outputs: ["out", "公司名称"] },
  input: { title: "输入项", color: "#16a085", inputs: ["上级"], outputs: ["out"] },
  value: {
    title: "数值源",
    color: "#d35400",
    inputs: ["实体引用", "乘输入", "输入键", "常量"],
    outputs: ["out"],
  },
  effect: {
    title: "效果",
    color: "#c0392b",
    inputs: ["上级", "参与方", "值", "值2"],
    outputs: [],
  },
  condition: {
    title: "检查",
    color: "#27ae60",
    inputs: ["上级", "值1", "值2"],
    outputs: [],
  },
  "list-op": {
    title: "列表运算",
    color: "#8e44ad",
    inputs: [],
    outputs: ["out"],
  },
  "dict-op": {
    title: "字典运算",
    color: "#16a085",
    inputs: [],
    outputs: ["out"],
  },
  calc: {
    title: "计算",
    color: "#117a8b",
    inputs: ["值A", "值B"],
    outputs: ["out"],
  },
  if: {
    title: "条件(IF)",
    color: "#e67e22",
    inputs: ["条件", "上级"],
    outputs: ["真分支", "假分支"],
  },
  foreach: {
    title: "遍历(FOREACH)",
    color: "#2c3e50",
    inputs: ["列表", "上级"],
    outputs: ["循环体"],
  },
  assign: {
    title: "赋值(ASSIGN)",
    color: "#7f8c8d",
    inputs: ["值", "上级"],
    outputs: [],
  },
  compare: {
    title: "布尔比较",
    color: "#c0392b",
    inputs: ["值A", "值B"],
    outputs: ["布尔"],
  },
  output: {
    title: "输出",
    color: "#2c3e50",
    inputs: ["值"],
    outputs: [],
  },
};

// ============ 属性面板选项（编辑器新增、graph-model 原有未导出的常量） ============
// 注意：ENTITY_TYPES / COMPARE_OPS / VALUE_TYPE_LABEL / COND_KIND_LABEL /
// EFFECT_KIND_LABEL / ENTITY_TYPE_LABEL / COMPARE_OP_LABEL 已在文件后部定义，
// 此处仅补充蓝图编辑器额外需要的几项，避免重复声明。
export const FIELD_OPS = ["ADD", "SUB", "SET"];
export const FIELD_OP_LABEL: Record<string, string> = {
  ADD: "加(+)",
  SUB: "减(-)",
  SET: "设为(=)",
};
// 效果「两值组合」运算符：value2 与 value 的组合方式。
export const VALUE_OPS = ["ADD", "SUB", "MUL"];
export const VALUE_OP_LABEL: Record<string, string> = {
  ADD: "加(+)",
  SUB: "减(-)",
  MUL: "乘(×)",
};
export const VALUE_TYPES = ["ENTITY", "FIELD", "CONST", "FORMULA", "INDUSTRY_IS"];
export const COND_KINDS = ["VALUE_COMPARE", "FIELD_COMPARE", "INDUSTRY_IS", "DICT_COMPARE", "LIST_COMPARE"];

// 数值互相比较（VALUE_COMPARE）支持的算子：两侧取数字比较 / 结构相等 / 包含 / 含键。
export const VALUE_COMPARE_OPS = ["GTE", "LTE", "GT", "LT", "EQ", "CONTAINS", "HAS_KEY"];

// 字典互相比较（DICT_COMPARE）支持的算子：仅数值比较三种，逐键 GTE / GT / EQ。
// 前提：值二（基准，如「所需原料」）的每个键都必须存在于值一（如「库存」）；
// 值一多出来的键（多余库存）不影响比较（值二键 ⊆ 值一键）。
export const DICT_COMPARE_OPS = ["GTE", "GT", "EQ"];

// 列表互相比较（LIST_COMPARE）支持的算子：元素相等 / 被包含 / 大于(长度) / 大于等于(长度) / 等于(长度)。
export const LIST_COMPARE_OPS = ["ELEMENT_EQ", "CONTAINS", "GT", "GTE", "EQ"];

// 布尔比较节点（compare）支持的算子：等式 / 不等式，输出布尔值（用于 IF 条件）。
export const BOOL_COMPARE_OPS = ["CMP_EQ", "CMP_NE", "CMP_GT", "CMP_LT", "CMP_GTE", "CMP_LTE"];

// ============ 运算节点（列表/字典）的 OP 定义 ============

// 每个 op 需要的输入参数 handle 列表（顺序即 args 顺序）。
export const OP_ARG_SPECS: Record<string, string[]> = {
  // 列表
  LIST_APPEND: ["list", "item1", "item2"],
  LIST_CONCAT: ["a", "b"],
  LIST_LEN: ["list"],
  LIST_CONTAINS: ["list", "item"],
  LIST_INDEX_OF: ["list", "item"],
  LIST_UNIQUE: ["list"],
  LIST_FLATTEN: ["list"],
  LIST_SUM_OF: ["list"],
  LIST_JOIN: ["list", "sep"],
  LIST_SLICE: ["list", "start", "end"],
  LIST_REVERSE: ["list"],
  LIST_SORT: ["list"],
  LIST_RANGE: ["start", "stop", "step"],
  LIST_ADD: ["a", "b"],
  LIST_SUB: ["a", "b"],
  // 字典
  DICT_GET: ["dict", "key", "default"],
  DICT_KEYS: ["dict"],
  DICT_VALUES: ["dict"],
  DICT_ENTRIES: ["dict"],
  DICT_HAS_KEY: ["dict", "key"],
  DICT_MERGE: ["a", "b"],
  DICT_FROM_PAIRS: ["pairs"],
  DICT_FROM_KEYS: ["keys", "value"],
  DICT_INVERT: ["dict"],
  DICT_ADD: ["a", "b"],
  DICT_SUB: ["a", "b"],
  DICT_APPEND: ["dict", "key", "value"],
  DICT_SUM: ["dict"],
  // 通用（归到列表运算节点渲染）
  LEN: ["x"],
  CONTAINS: ["coll", "item"],
  SUM_OF: ["list"],
  // 算术（归到计算节点 calc 渲染）：左操作数 / 右操作数
  ADD: ["left", "right"],
  SUB: ["left", "right"],
  MUL: ["left", "right"],
  DIV: ["left", "right"],
  // 高级算术
  EXP: ["operand"], // e^operand（自然指数）
  LOG: ["operand", "base"], // 对数：base 缺省时按自然对数 e 计算
  MIN: ["left", "right"], // 取两个数值的最小值
  MAX: ["left", "right"], // 取两个数值的最大值
  // 比较（返回布尔）：值A op 值B，用于 IF 条件"输出布尔值的条件式"
  CMP_EQ: ["left", "right"],
  CMP_NE: ["left", "right"],
  CMP_GT: ["left", "right"],
  CMP_LT: ["left", "right"],
  CMP_GTE: ["left", "right"],
  CMP_LTE: ["left", "right"],
};

// 算术运算 op（计算节点 calc 暴露这些）。
export const ARITH_OPS = ["ADD", "SUB", "MUL", "DIV", "EXP", "LOG", "MIN", "MAX"];

// 每个 op 的中文标题（供下拉与节点摘要显示）。
export const OP_LABELS_FULL: Record<string, string> = {
  LIST_APPEND: "追加元素",
  LIST_CONCAT: "拼接多个列表",
  LIST_LEN: "列表长度",
  LIST_CONTAINS: "是否包含元素",
  LIST_INDEX_OF: "元素位置",
  LIST_UNIQUE: "去重",
  LIST_FLATTEN: "扁平化",
  LIST_SUM_OF: "列表求和",
  LIST_JOIN: "用分隔符连接",
  LIST_SLICE: "切片",
  LIST_REVERSE: "反转",
  LIST_SORT: "排序",
  LIST_RANGE: "生成整数区间",
  LIST_ADD: "列表相加",
  LIST_SUB: "列表相减",
  DICT_GET: "取字典值",
  DICT_KEYS: "取全部键",
  DICT_VALUES: "取全部值",
  DICT_ENTRIES: "转键值对数组",
  DICT_HAS_KEY: "是否含键",
  DICT_MERGE: "合并多个字典",
  DICT_FROM_PAIRS: "由键值对造字典",
  DICT_FROM_KEYS: "由键列表造字典",
  DICT_INVERT: "键值互换",
  DICT_ADD: "字典相加",
  DICT_SUB: "字典相减",
  DICT_APPEND: "字典追加元素",
  DICT_SUM: "字典求和",
  LEN: "长度(通用)",
  CONTAINS: "包含(通用)",
  SUM_OF: "求和(通用)",
  ADD: "加(+)",
  SUB: "减(−)",
  MUL: "乘(×)",
  DIV: "除(÷)",
  EXP: "指数(e^x)",
  LOG: "对数(log)",
  MIN: "取最小(min)",
  MAX: "取最大(max)",
  CMP_EQ: "等于(=)",
  CMP_NE: "不等于(≠)",
  CMP_GT: "大于(>)",
  CMP_LT: "小于(<)",
  CMP_GTE: "大于等于(≥)",
  CMP_LTE: "小于等于(≤)",
};

// 参数 handle 的中文标签（编辑面板显示）。
export const OP_ARG_LABELS: Record<string, string> = {
  list: "列表",
  item: "元素",
  item1: "元素1",
  item2: "元素2",
  a: "列表A",
  b: "列表B",
  coll: "集合",
  dict: "字典",
  key: "键",
  default: "默认值",
  pairs: "键值对列表",
  keys: "键列表",
  value: "值",
  sep: "分隔符",
  start: "起始",
  stop: "结束",
  step: "步长",
  x: "集合",
  left: "值A",
  right: "值B",
  operand: "操作数",
  base: "底数(默认e)",
};

// op 归类（决定用哪个调色板节点类型渲染）。
export function opCategory(op: string): "list" | "dict" | "arith" | "compare" {
  if (op.startsWith("DICT_")) return "dict";
  if (ARITH_OPS.includes(op)) return "arith";
  if (op.startsWith("CMP_")) return "compare";
  return "list";
}

// 运算节点的输入端口（依所选 op 动态计算）。
export function nodeInputs(node: GNode): string[] {
  if (node.type === "list-op" || node.type === "dict-op" || node.type === "calc" || node.type === "compare") {
    return OP_ARG_SPECS[node.data.op as string] || [];
  }
  // 原料清单输入源额外暴露一个「参与方」输入端口：连上参与方后，
  // 其「原料总价格」端点会按该参与方产业类型的「所在地」节点价格筛选（地点价优先）。
  if (node.type === "input" && node.data.type === "materialList") return ["parent", "party"];
  // 基建清单输入源额外暴露一个「基建列表」输入端口：连上一个基建名称数组后，
  // 前端创建合同时该清单的基建下拉仅展示列表中的基建；未连接则展示全部基建。
  if (node.type === "input" && node.data.type === "infrastructureList") return ["parent", "infraList"];
  // 载具清单输入源额外暴露一个「载具列表」输入端口：连上一个载具名称数组后，
  // 前端创建合同时该清单的载具下拉仅展示列表中的载具；未连接则展示全部载具。
  if (node.type === "input" && node.data.type === "vehicleList") return ["parent", "vehList"];
  if (node.type === "condition") {
    // 检查节点的输入端口随种类变化：
    //  - VALUE_COMPARE：两个自由数值源（值1 / 值2），都比某参与方字段更灵活；
    //  - FIELD_COMPARE：兼容旧版，左操作数为某参与方字段 + 右侧值；
    //  - INDUSTRY_IS：仅需参与方。
    // 所有种类都额外在首位暴露一个「上级」控制流端口：连到 IF 的真/假分支后，
    // 该检查仅在所属 IF 分支条件成立时才执行（引擎 runConditions 按分支短路）。
    const k = node.data.condKind as string;
    if (k === "INDUSTRY_IS") return ["parent", "party"];
    if (k === "FIELD_COMPARE") return ["parent", "party", "value"];
    if (k === "DICT_COMPARE") return ["parent", "value1", "value2"];
    if (k === "LIST_COMPARE") return ["parent", "value1", "value2"];
    return ["parent", "value1", "value2"];
  }
  return NODE_PORTS[node.type]?.inputs || [];
}

// 运算节点的输出端口。
export function nodeOutputs(node: GNode): string[] {
  if (node.type === "list-op" || node.type === "dict-op" || node.type === "calc" || node.type === "compare") return ["out"];
  // 原料清单输入源额外暴露「碳排放合计」「原料总价格」端点（分别按比赛查
  // 每种原料的 碳排放系数×数量、价格×数量 之和），供下游数值源/公式节点连线引用。
  if (node.type === "input" && node.data.type === "materialList")
    return ["out", "carbon", "price", "materialQty"];
  // 节点列表输入源额外暴露「路程」「路径类型」「起始节点名」「终止节点名」端点。
  if (node.type === "input" && node.data.type === "nodeRoute") return ["out", "distance", "pathTypes", "startNodeName", "endNodeName"];
  // 零件清单输入源额外暴露「所需原料」「所需的科技节点」「零件总件数」「所需原料总数量」端点。
  //  - materials：按比赛展开每个零件的配比 → 原料→数量 字典；
  //  - techNodes：按比赛查每个零件所需的科技节点(TechNode)名称，去重返回字符串数组。
  //  - partQty：清单字典各零件数量之和；
  //  - partMaterialQty：展开配比后所有原料数量之和（所需原料总件数）。
  if (node.type === "input" && node.data.type === "partList")
    return ["out", "materials", "techNodes", "partQty", "partMaterialQty"];
  // 产品清单输入源额外暴露「需要的零件」「所需的科技节点」「产品总件数」「所需零件总数量」端点。
  //  - parts：按比赛展开每个产品的配比 → 零件→数量 字典；
  //  - techNodes：按比赛查每个产品所需的科技节点(TechNode)名称，去重返回字符串数组。
  //  - productQty：清单字典各产品数量之和；
  //  - productPartQty：展开配比后所有零件数量之和（所需零件总件数）。
  if (node.type === "input" && node.data.type === "productList")
    return ["out", "parts", "techNodes", "productQty", "productPartQty"];
  // 基建清单输入源额外暴露 8 个聚合端点：各 Σ(数值字段 × 数量)，
  // 供下游数值源/公式节点连线引用，无需手动逐项乘加。
  if (node.type === "input" && node.data.type === "infrastructureList")
    return [
      "out",
      "infraPrice",
      "infraFootprint",
      "infraEmployment",
      "infraPopulation",
      "infraHighQuality",
      "infraHappiness",
      "infraIncome",
      "infraCarbon",
      "infraActivationPrice",
    ];
  // 科技树节点输入源：单选一个科技节点，额外暴露「前置节点」「研发费用」端点。
  if (node.type === "input" && node.data.type === "techNode")
    return ["out", "prerequisites", "researchCost"];
  // 燃料清单输入源额外暴露「燃料总数量」「燃料总价格」端点。
  //  - fuelQty：清单字典中各燃料数量之和（无需查库）；
  //  - fuelPrice：按比赛查每种燃料 pricePerLiter × 数量 之和（需查库）。
  if (node.type === "input" && node.data.type === "fuelList")
    return ["out", "fuelQty", "fuelPrice"];
  // 载具清单输入源额外暴露「载具总价格」及三个载具属性聚合端点：
  //  - vehiclePrice：按比赛查每种载具 price × 数量 之和（需查库）。
  //  - vehicleCargo：按比赛查每种载具 maxCargo × 数量 之和（需查库）。
  //  - vehicleFuelPerKm：按比赛查每种载具 fuelConsumptionPerKm × 数量 之和（需查库）。
  //  - vehicleCarbon：按比赛查每种载具 carbonEmission × 数量 之和（不乘每公里油耗，需查库）。
  if (node.type === "input" && node.data.type === "vehicleList")
    return ["out", "vehiclePrice", "vehicleCargo", "vehicleFuelPerKm", "vehicleCarbon"];
  // 仓库清单输入源额外暴露「每种种类的仓库总存储量」「仓库总价格」端点。
  //  - warehouseStorage：按比赛查每个仓库的 type + capacity，将「capacity × 数量」按 type 累加，
  //    输出 {仓库种类(type): 总存储量} 字典（如 {"MATERIAL": 1200, "PRODUCT": 800}）。
  //  - warehousePrice：按比赛查每个仓库的 price × 数量 之和。
  if (node.type === "input" && node.data.type === "warehouseList")
    return ["out", "warehouseStorage", "warehousePrice"];
  return NODE_PORTS[node.type]?.outputs || [];
}

// 端口中文→内部 handle 的映射（编辑器显示用，序列化用内部名）。
export const PORT_LABEL_TO_HANDLE: Record<string, string> = {
  实体引用: "entityRef",
  乘输入: "multiplyByInput",
  输入键: "key",
  常量: "value",
  参与方: "party",
  公司名称: "companyName",
  值: "value",
  值1: "value1",
  值2: "value2",
  上级: "parent",
  条件: "cond",
  列表: "items",
  真分支: "then",
  假分支: "else",
  循环体: "body",
  输出: "out",
  碳排放合计: "carbon",
  原料总价格: "price",
  原料总数量: "materialQty",
  零件总件数: "partQty",
  产品总件数: "productQty",
  燃料总数量: "fuelQty",
  燃料总价格: "fuelPrice",
  载具总价格: "vehiclePrice",
  载具总载货量: "vehicleCargo",
  总每公里油耗: "vehicleFuelPerKm",
  总碳排数: "vehicleCarbon",
  每种种类的仓库总存储量: "warehouseStorage",
  仓库总价格: "warehousePrice",
  路程: "distance",
  存在的路径类型: "pathTypes",
  起始节点名: "startNodeName",
  终止节点名: "endNodeName",
  所需原料: "materials",
  所需原料总数量: "partMaterialQty",
  需要的零件: "parts",
  所需零件总数量: "productPartQty",
  所需的科技节点: "techNodes",
  基建总价格: "infraPrice",
  基建总占地面积: "infraFootprint",
  基建总就业率加成: "infraEmployment",
  基建总人口加成: "infraPopulation",
  基建总高素质人口加成: "infraHighQuality",
  基建总幸福度加成: "infraHappiness",
  基建总人均收益加成: "infraIncome",
  基建总减碳排放加成: "infraCarbon",
  基建启用总费用: "infraActivationPrice",
  基建列表: "infraList",
  载具列表: "vehList",
  前置节点: "prerequisites",
  研发费用: "researchCost",
};

export const PORT_HANDLE_TO_LABEL: Record<string, string> = Object.fromEntries(
  Object.entries(PORT_LABEL_TO_HANDLE).map(([k, v]) => [v, k]),
);

// 端口"干什么"的用途说明（画布悬停提示用）。key 为端口 handle。
export const PORT_DESC: Record<string, string> = {
  entityRef: "选择要读取属性的数据管理实体（通常连输入项，提供实体 id）",
  multiplyByInput: "可选：再乘以某个输入项的数量",
  key: "连接一个字符串源，作为字典键（如 CONST 字符串 / 另一个字典运算的输出）",
  value: "连接一个上游数值源，或直接填常量值",
  parent: "挂到某个控制流（IF / FOREACH / ASSIGN）之下，成为其分支 / 循环体 / 语句",
  party: "连接参与方节点，指定效果或检查作用于哪一方",
  companyName: "该参与方所绑定公司的名称（运行时取公司 name 字段）",
  value1:
    "连接一个数值源节点（FIELD/ENTITY/INPUT/FORMULA/OP…）作为比较的左操作数（自动获得，不写死常量）",
  value2: "连接一个数值源节点作为比较的右操作数（自动获得，不写死常量）",
  fieldParty: "连接参与方节点：读取该方公司当前的产业字段值作为本节点的输出",
  routeRef: "连接一个「节点列表」类型的输入项：创建合同时由用户逐个选择地图节点，引擎按此算路程",
  cond: "连接数值源或运算节点，作为 IF 判断条件的取值",
  items: "连接数值源或列表运算节点（须为数组），作为 FOREACH 遍历对象",
  left: "连接第一个数值源（被加数 / 被减数 / 被乘数 / 被除数），可连 value / input / 其它运算节点",
  right: "连接第二个数值源（加数 / 减数 / 乘数 / 除数），可连 value / input / 其它运算节点",
  then: "连到子效果 / 控制流节点的「上级」输入：条件为真时执行",
  else: "连到子效果 / 控制流节点的「上级」输入：条件为假时执行",
  body: "连到子效果节点的「上级」输入：每轮迭代执行",
  out: "本节点计算结果输出，可接入下游的「值」端口",
  carbon:
    "碳排放合计：按比赛查询每种原料的「碳排放系数(浮点) × 输入数量」之和，作为单个浮点数输出，可接入下游的数值端口",
  price:
    "原料总价格：按比赛查询每种原料的「价格(浮点) × 输入数量」之和，作为单个浮点数输出，可接入下游的数值端口",
  materialQty:
    "原料总数量：直接把清单字典中各原料的数量相加，输出单个浮点数，可接入下游的数值端口（效果/检查计算），无需查表",
  partQty:
    "零件总件数：直接把清单字典中各零件的数量相加，输出单个浮点数，可接入下游的数值端口（效果/检查计算），无需查表",
  productQty:
    "产品总件数：直接把清单字典中各产品的数量相加，输出单个浮点数，可接入下游的数值端口（效果/检查计算），无需查表",
  fuelQty:
    "燃料总数量：直接把清单字典中各燃料的数量相加，输出单个浮点数，可接入下游的数值端口（效果/检查计算），无需查表",
  fuelPrice:
    "燃料总价格：按比赛查询每种燃料的「每升价格(pricePerLiter) × 输入数量」之和，作为单个浮点数输出，可接入下游的数值端口（效果/检查计算）",
  vehiclePrice:
    "载具总价格：按比赛查询每种载具的「价格(price) × 输入数量」之和，作为单个浮点数输出，可接入下游的数值端口（效果/检查计算）",
  vehicleCargo:
    "载具总载货量：按比赛查询每种载具的「载货量(maxCargo) × 输入数量」之和，作为单个浮点数输出，可接入下游的数值端口（效果/检查计算）",
  vehicleFuelPerKm:
    "总每公里油耗：按比赛查询每种载具的「每公里油耗(fuelConsumptionPerKm) × 输入数量」之和，作为单个浮点数输出，可接入下游的数值端口（效果/检查计算）",
  vehicleCarbon:
    "总碳排数：按比赛查询每种载具的「碳排放系数(carbonEmission) × 输入数量」之和（不乘每公里油耗），作为单个浮点数输出，可接入下游的数值端口（效果/检查计算）",
  warehouseStorage:
    "每种种类的仓库总存储量：按比赛查询每个仓库的「种类(type) + 容量(capacity)」，将清单中「容量 × 数量」按种类(type)累加，输出 {仓库种类: 总存储量} 字典（如 {MATERIAL: 1200, PRODUCT: 800}），可接入下游的字典/公式端口",
  warehousePrice:
    "仓库总价格：按比赛查询每种仓库的「价格(price) × 输入数量」之和，作为单个浮点数输出，可接入下游的数值端口（效果/检查计算）",
  materials:
    "所需原料：按比赛查询每个零件的「配比(原料→系数)」，将清单中零件的「数量 × 系数」按原料累加，输出 {原料名称: 总数量} 字典，可接入下游的字典/公式端口",
  partMaterialQty:
    "所需原料总数量：按比赛查询每个零件的配比，展开为原料字典后把所有原料数量求和（Σ 零件数量 × 各原料配比），输出单个浮点数（所需原料的总件数），可接入下游的数值端口（效果/检查计算）",
  parts:
    "需要的零件：按比赛查询每个产品的「配比(零件→系数)」，将清单中产品的「数量 × 系数」按零件累加，输出 {零件名称: 总数量} 字典，可接入下游的字典/公式端口",
  productPartQty:
    "所需零件总数量：按比赛展开每个产品的零件配比后把所有零件数量求和，输出单个浮点数（所需零件的总件数），可接入下游的数值端口（效果/检查计算）",
  techNodes:
    "所需的科技节点：按比赛查询清单中每个零件/产品的「科技需求(TechNode)」，收集这些科技节点的名称，去重后输出字符串数组，可接入下游的列表/字典端口",
  infraPrice:
    "基建总价格：按比赛查询每种基建的「价格(price) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口（效果/检查计算）",
  infraFootprint:
    "基建总占地面积：按比赛查询每种基建的「footprint(占地) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口",
  infraEmployment:
    "基建总就业率加成：按比赛查询每种基建的「employmentRateBonus(就业率加成) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口",
  infraPopulation:
    "基建总人口加成：按比赛查询每种基建的「populationBonus(人口加成) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口",
  infraHighQuality:
    "基建总高素质人口加成：按比赛查询每种基建的「highQualityPopulationBonus(高素质人口加成) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口",
  infraHappiness:
    "基建总幸福度加成：按比赛查询每种基建的「happinessIndexBonus(幸福度加成) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口",
  infraIncome:
    "基建总人均收益加成：按比赛查询每种基建的「perCapitaIncomeBonus(人均收益加成) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口",
  infraCarbon:
    "基建总减碳排放加成：按比赛查询每种基建的「carbonReductionBonus(减碳排放加成) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口",
  infraActivationPrice:
    "基建启用总费用：按比赛查询每种基建的「activationPrice(激活价格) × 输入数量」之和，作为单个浮点数输出，可接入下游数值端口（效果/检查计算）",
  prerequisites:
    "前置节点：按比赛查询该科技树节点的全部前置依赖节点（经 TechPrerequisite.prerequisite 关联），输出前置节点名称的字符串数组，可接入下游的列表/字典端口",
  researchCost:
    "研发费用：按比赛查询该科技树节点的 researchCost（Float），作为单个浮点数输出，可接入下游数值端口（效果/检查计算）",
  infraList:
    "基建列表：可选。传入一个基建名称的数组（如 CONST 列表节点或列表输入项的默认值），创建合同时该基建清单的下拉仅展示列表中的基建；未连接则展示全部基建",
  vehList:
    "载具列表：可选。传入一个载具名称的数组（如 CONST 列表节点或列表输入项的默认值），创建合同时该载具清单的下拉仅展示列表中的载具；未连接则展示全部载具",
};

// 端口"流动的值是什么数据类型"的中文标注（key 为端口 handle）。
// 仅覆盖类型固定、不随节点配置变化的端口；动态端口（输入项/数值源/运算节点）
// 由下方派生函数给出。
export const PORT_TYPE: Record<string, string> = {
  entityRef: "实体ID",
  multiplyByInput: "数量",
  key: "字符串",
  value: "常量/任意",
  routeRef: "节点列表",
  fieldParty: "参与方",
  infraList: "列表(基建名)",
  vehList: "列表(载具名)",
  parent: "控制流",
  party: "参与方",
  companyName: "字符串",
  value1: "数值/任意",
  value2: "数值/任意",
  cond: "布尔/数值",
  items: "列表",
  left: "数值",
  right: "数值",
  then: "控制流",
  else: "控制流",
  body: "控制流",
  out: "任意值",
  price:
    "价格：按比赛查询每种原料的价格，将「价格 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口（效果/检查计算）",
  materialQty:
    "总数：把清单字典中各原料的数量直接相加，作为单个浮点数输出，可接入下游数值端口",
  partQty:
    "总数：把清单字典中各零件的数量直接相加，作为单个浮点数输出，可接入下游数值端口",
  productQty:
    "总数：把清单字典中各产品的数量直接相加，作为单个浮点数输出，可接入下游数值端口",
  fuelQty:
    "总数：把清单字典中各燃料的数量直接相加，作为单个浮点数输出，可接入下游数值端口",
  fuelPrice:
    "价格：按比赛查询每种燃料的每升价格，将「每升价格 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口",
  vehiclePrice:
    "价格：按比赛查询每种载具的价格，将「价格 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口",
  vehicleCargo:
    "载货量：按比赛查询每种载具的载货量(maxCargo)，将「载货量 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口",
  vehicleFuelPerKm:
    "油耗：按比赛查询每种载具的每公里油耗(fuelConsumptionPerKm)，将「每公里油耗 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口",
  vehicleCarbon:
    "碳排：按比赛查询每种载具的碳排放系数(carbonEmission)，将「碳排放系数 × 输入数量」求和（不乘每公里油耗），作为单个浮点数输出，可接入下游数值端口",
  warehouseStorage:
    "字典(种类→总存储量)：{仓库种类(type): 总存储量}，由清单中仓库容量 × 数量按种类累加得到，可直接接入字典/公式端口",
  warehousePrice:
    "价格：按比赛查询每种仓库的价格，将「价格 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口",
  distance:
    "路程：按比赛查询地图相邻节点最短路径距离之和，作为单个浮点数输出，可接入下游数值端口（效果/检查计算）",
  pathTypes:
    "存在的路径类型：按比赛查询与本节点列表任一路点相连的边，取这些边所用路径类型的名称组成列表（字符串数组），可接入下游列表/字典端口",
  startNodeName:
    "起始节点名：节点列表第一个节点的名称（字符串），可接入下游文本/公式端口",
  endNodeName:
    "终止节点名：节点列表最后一个节点的名称（字符串），可接入下游文本/公式端口",
  materials:
    "字典(原料→数量)：{原料名称: 总数量}，由清单中零件数量与配比展开得到，可直接接入字典/公式端口",
  parts:
    "字典(零件→数量)：{零件名称: 总数量}，由清单中产品数量与配比展开得到，可直接接入字典/公式端口",
  techNodes:
    "列表(科技节点名)：字符串数组，由清单中零件/产品所需科技节点名称组成（去重），可直接接入列表/字典端口",
  infraPrice:
    "价格：按比赛查询每种基建的价格，将「价格 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口",
  infraFootprint:
    "占地面积：按比赛查询每种基建的 footprint，将「footprint × 输入数量」求和，作为单个浮点数输出",
  infraEmployment:
    "就业率加成：按比赛查询每种基建的 employmentRateBonus，将「employmentRateBonus × 输入数量」求和，浮点数输出",
  infraPopulation:
    "人口加成：按比赛查询每种基建的 populationBonus，将「populationBonus × 输入数量」求和，浮点数输出",
  infraHighQuality:
    "高素质人口加成：按比赛查询每种基建的 highQualityPopulationBonus，将「highQualityPopulationBonus × 输入数量」求和，浮点数输出",
  infraHappiness:
    "幸福度加成：按比赛查询每种基建的 happinessIndexBonus，将「happinessIndexBonus × 输入数量」求和，浮点数输出",
  infraIncome:
    "人均收益加成：按比赛查询每种基建的 perCapitaIncomeBonus，将「perCapitaIncomeBonus × 输入数量」求和，浮点数输出",
  infraCarbon:
    "减碳排放加成：按比赛查询每种基建的 carbonReductionBonus，将「carbonReductionBonus × 输入数量」求和，浮点数输出",
  infraActivationPrice:
    "价格：按比赛查询每种基建的激活价格，将「激活价格 × 输入数量」求和，作为单个浮点数输出，可接入下游数值端口",
  prerequisites:
    "列表(科技节点名)：字符串数组，由该科技树节点的全部前置依赖节点名称组成，可直接接入列表/字典端口",
  researchCost:
    "研发费用：按比赛查询该科技树节点的 researchCost，作为单个浮点数输出，可接入下游数值端口",
};

// 输入项(input)节点的输出端口数据类型：取决于该输入项的类型声明。
export function inputOutType(type: string): string {
  switch (type) {
    case "number":
      return "数字";
    case "string":
      return "文本";
    case "boolean":
      return "布尔";
    case "ENTITY":
      return "实体ID";
    case "nodeRoute":
      return "节点列表[]";
    case "list":
      return "列表";
    case "dict":
      return "字典";
    case "materialList":
      return "字典(原料→数量)";
    case "partList":
      return "字典(零件→数量)";
    case "productList":
      return "字典(产品→数量)";
    case "infrastructureList":
      return "字典(基建→数量)";
    case "fuelList":
      return "字典(燃料→数量)";
    case "vehicleList":
      return "字典(载具→数量)";
    case "warehouseList":
      return "字典(仓库→数量)";
    case "techNode":
      return "科技节点名(单值)";
    default:
      return "任意值";
  }
}

// 产业字段现值(FIELD)值源节点的输出端口类型：随所选字段的数据类型变化。
const FIELD_PORT_TYPE: Record<string, string> = {
  STRING: "文本",
  NUMBER: "数字",
  BOOLEAN: "布尔",
  LIST: "列表",
  DICTIONARY: "字典",
};

// 数值源(value)节点的输出端口数据类型：取决于其取值方式。
export function valueOutType(valueType: string, fieldType?: string): string {
  switch (valueType) {
    case "ENTITY":
      return "属性值(数字/文本)";
    case "INPUT":
      return "输入值(随输入项类型)";
    case "CONST":
      return "常量(任意)";
    case "FORMULA":
      return "数字";
    case "VAR":
      return "变量值";
    case "ROUTE":
      return "数字(路程)";
    case "FIELD":
      return FIELD_PORT_TYPE[fieldType || ""] || "数字(产业字段)";
    case "INDUSTRY_IS":
      return "布尔";
    default:
      return "任意值";
  }
}

// 运算节点参数端口的数据类型（按参数 handle 粗分类）。
export function opArgType(handle: string): string {
  switch (handle) {
    case "list":
    case "coll":
    case "x":
      return "列表/字典";
    case "dict":
      return "字典";
    case "a":
      return "列表";
    case "b":
      return "列表/字典";
    case "item":
    case "item1":
    case "item2":
      return "元素(任意)";
    case "key":
      return "键";
    case "default":
      return "值(任意)";
    case "pairs":
      return "键值对[]";
    case "keys":
      return "键[]";
    case "value":
      return "值(任意)";
    case "sep":
      return "文本";
    case "start":
    case "stop":
    case "step":
      return "数字";
    case "left":
    case "right":
      return "数值";
    default:
      return "任意值";
  }
}

// 运算节点的输出端口数据类型：按 op 推导（列表/字典/算术）。
export function opOutputType(op: string): string {
  if (op.startsWith("CMP_")) return "布尔";
  if (ARITH_OPS.includes(op)) return "数字";
  if (op === "LIST_LEN" || op === "LEN" || op === "LIST_INDEX_OF" || op === "LIST_SUM_OF" || op === "SUM_OF")
    return "数字";
  if (op === "LIST_CONTAINS" || op === "CONTAINS") return "布尔";
  if (op === "LIST_JOIN") return "文本(字符串)";
  if (op.startsWith("DICT_")) {
    if (op === "DICT_KEYS" || op === "DICT_VALUES" || op === "DICT_ENTRIES") return "列表";
    if (op === "DICT_HAS_KEY") return "布尔";
    if (op === "DICT_GET") return "值(任意)";
    if (op === "DICT_SUM") return "数字";
    return "字典";
  }
  // 其余列表运算（Append/Concat/Unique/Flatten/Reverse/Slice/Range）
  return "列表";
}

// 数值源(value)节点按 valueType 过滤后的输入端口 handle 列表（与编辑器 inputHandles 一致）。
export function nodeInputHandles(node: GNode): string[] {
  if (node.type === "root") return [];
  if (node.type === "value") {
    const vt = node.data.valueType;
    if (vt === "ENTITY") return ["entityRef", "multiplyByInput"];
    if (vt === "INPUT") return ["key"];
    if (vt === "CONST") return ["value"];
    if (vt === "ROUTE") return ["routeRef"];
    if (vt === "FIELD") return ["fieldParty"];
    if (vt === "INDUSTRY_IS") return ["fieldParty"];
    return [];
  }
  return nodeInputs(node);
}

// 综合入口：返回某节点某端口（输入/输出）流动值的数据类型。
export function portDataType(node: GNode, kind: "in" | "out", idx: number): string {
  if (kind === "out") {
    if (node.type === "root") return "（结构连接）";
    if (node.type === "party") {
      if (nodeOutputs(node)[idx] === "companyName") return "字符串(公司名称)";
      return "参与方引用";
    }
    if (node.type === "input") {
      // materialList 输入节点有第二、第三输出端口 carbon / price：
      // Σ(碳排放系数[Float] × 数量) / Σ(价格[Float] × 数量)，结果均为浮点数
      if (nodeOutputs(node)[idx] === "carbon") return "浮点数(碳排放合计)";
      if (nodeOutputs(node)[idx] === "price") return "浮点数(原料总价格)";
      // materialList 输入节点有第四个输出端口 materialQty：清单字典各原料数量之和，浮点数
      if (nodeOutputs(node)[idx] === "materialQty") return "浮点数(原料总数量)";
      // partList 输入节点有第四个输出端口 partQty：清单字典各零件数量之和，浮点数
      if (nodeOutputs(node)[idx] === "partQty") return "浮点数(零件总件数)";
      // partList 输入节点有第五个输出端口 partMaterialQty：展开配比后所有原料数量之和，浮点数
      if (nodeOutputs(node)[idx] === "partMaterialQty") return "浮点数(所需原料总数量)";
      // productList 输入节点有第四个输出端口 productQty：清单字典各产品数量之和，浮点数
      if (nodeOutputs(node)[idx] === "productQty") return "浮点数(产品总件数)";
      // productList 输入节点有第五个输出端口 productPartQty：展开配比后所有零件数量之和，浮点数
      if (nodeOutputs(node)[idx] === "productPartQty") return "浮点数(所需零件总数量)";
      if (nodeOutputs(node)[idx] === "fuelQty") return "浮点数(燃料总数量)";
      if (nodeOutputs(node)[idx] === "fuelPrice") return "浮点数(燃料总价格)";
      if (nodeOutputs(node)[idx] === "vehiclePrice") return "浮点数(载具总价格)";
      if (nodeOutputs(node)[idx] === "vehicleCargo") return "浮点数(载具总载货量)";
      if (nodeOutputs(node)[idx] === "vehicleFuelPerKm") return "浮点数(总每公里油耗)";
      if (nodeOutputs(node)[idx] === "vehicleCarbon") return "浮点数(总碳排数)";
      if (nodeOutputs(node)[idx] === "warehouseStorage") return "字典(种类→总存储量)";
      if (nodeOutputs(node)[idx] === "warehousePrice") return "浮点数(仓库总价格)";
      // nodeRoute 输入节点有第二个输出端口 distance：相邻节点最短路距离之和，浮点数
      if (nodeOutputs(node)[idx] === "distance") return "浮点数(路程)";
      // nodeRoute 输入节点有第三个输出端口 pathTypes：与任一路点相连的边所用路径类型名称列表
      if (nodeOutputs(node)[idx] === "pathTypes") return "列表(路径类型名)";
      // nodeRoute 输入节点有第四、五个输出端口：起始/终止节点名称
      if (nodeOutputs(node)[idx] === "startNodeName") return "字符串(起始节点名)";
      if (nodeOutputs(node)[idx] === "endNodeName") return "字符串(终止节点名)";
      // partList 输入节点有第二个输出端口 materials：零件配比展开后的原料字典，字典(原料→数量)
      if (nodeOutputs(node)[idx] === "materials") return "字典(原料→数量)";
      // productList 输入节点有第二个输出端口 parts：产品配比展开后的零件字典，字典(零件→数量)
      if (nodeOutputs(node)[idx] === "parts") return "字典(零件→数量)";
      // partList / productList 输入节点有第三个输出端口 techNodes：清单中零件/产品所需科技节点名列表
      if (nodeOutputs(node)[idx] === "techNodes") return "列表(科技节点名)";
      // infrastructureList 输入节点有 8 个聚合输出端口：各 Σ(数值字段 × 数量)
      if (nodeOutputs(node)[idx] === "infraPrice") return "浮点数(基建总价格)";
      if (nodeOutputs(node)[idx] === "infraFootprint") return "浮点数(基建总占地面积)";
      if (nodeOutputs(node)[idx] === "infraEmployment") return "浮点数(基建总就业率加成)";
      if (nodeOutputs(node)[idx] === "infraPopulation") return "浮点数(基建总人口加成)";
      if (nodeOutputs(node)[idx] === "infraHighQuality") return "浮点数(基建总高素质人口加成)";
      if (nodeOutputs(node)[idx] === "infraHappiness") return "浮点数(基建总幸福度加成)";
      if (nodeOutputs(node)[idx] === "infraIncome") return "浮点数(基建总人均收益加成)";
      if (nodeOutputs(node)[idx] === "infraCarbon") return "浮点数(基建总减碳排放加成)";
      if (nodeOutputs(node)[idx] === "infraActivationPrice") return "浮点数(基建启用总费用)";
      // techNode 输入节点有第二、第三输出端口：前置节点列表 / 研发费用
      if (nodeOutputs(node)[idx] === "prerequisites") return "列表(科技节点名)";
      if (nodeOutputs(node)[idx] === "researchCost") return "浮点数(研发费用)";
      return inputOutType(node.data.type || "number");
    }
    if (node.type === "value") return valueOutType(node.data.valueType || "INPUT", node.data.fieldType);
    if (node.type === "list-op" || node.type === "dict-op" || node.type === "calc" || node.type === "compare")
      return opOutputType(node.data.op as string);
    const h = nodeOutputs(node)[idx];
    return PORT_TYPE[h] || "任意值";
  }
  // 检查节点：字典相互比较（DICT_COMPARE）的值1/值2 端口流动的是字典，
  // 便于类型连线校验放行字典源（CONST 字典 / 字典运算节点 / 清单聚合字典等）。
  if (node.type === "condition" && node.data.condKind === "DICT_COMPARE") return "字典";
  if (node.type === "condition" && node.data.condKind === "LIST_COMPARE") return "列表";
  const h = nodeInputHandles(node)[idx];
  return PORT_TYPE[h] || "任意值";
}

// ============ 连线两端的类型兼容性检查（编辑器红线警告用） ============
export interface EdgeTypeCheck {
  ok: boolean;
  sourceType: string;
  targetType: string;
  reason?: string;
}

// 取某节点某输出端口（按 handle）的数据类型。
function outPortTypeOf(s: GNode, handle?: string): string {
  const outs = nodeOutputs(s);
  let idx = handle ? outs.indexOf(handle) : outs.length - 1;
  if (idx < 0) idx = 0;
  return portDataType(s, "out", idx);
}
// 取某节点某输入端口（按 handle）的数据类型。
function inPortTypeOf(t: GNode, handle?: string): string {
  const ins = nodeInputHandles(t);
  let idx = handle ? ins.indexOf(handle) : ins.length - 1;
  if (idx < 0) idx = 0;
  return portDataType(t, "in", idx);
}

function isNumberLike(t: string): boolean {
  // 同时接受"数字"与"浮点"两类数值标注，便于浮点结果（如碳排放合计）接入数值端口
  return t.includes("数字") || t.includes("浮点");
}
function isListLike(t: string): boolean {
  return (
    t.includes("列表") ||
    t.includes("字典") ||
    t.includes("键值对") ||
    t.includes("键[]") ||
    t.includes("节点路径") ||
    t.includes("节点列表")
  );
}
function isDictLike(t: string): boolean {
  return t.includes("字典");
}

// 判断 target 端口（类型 tType）能否接收 source 类型 sType（已在外层豁免"任意"）。
function typeAccepts(tType: string, sType: string): boolean {
  if (tType === "实体ID") return sType === "实体ID";
  if (tType === "参与方" || tType === "参与方引用") return sType.includes("参与方");
  if (tType === "数量" || tType === "数值") return isNumberLike(sType);
  if (tType === "布尔/数值") return isNumberLike(sType) || sType.includes("布尔");
  if (tType === "节点列表") return isListLike(sType) || sType.includes("节点路径");
  if (tType === "列表") return isListLike(sType);
  if (tType === "字典") return isDictLike(sType);
  // 文本 / 键 / 键值对[] / 键[] 等宽松端口：一律放行，避免误报。
  return true;
}

// 检查一条连线两端类型是否兼容。返回 ok 与两端类型，不匹配时附带中文原因。
export function edgeTypeCheck(graph: GGraph, edge: GEdge): EdgeTypeCheck {
  const s = nodeById(graph, edge.source);
  const t = nodeById(graph, edge.target);
  if (!s || !t) return { ok: true, sourceType: "", targetType: "" };

  // 结构挂载：根节点 → 输入项/参与方 的 targetHandle="in"（无数据语义），一律放行。
  if (edge.targetHandle === "in") {
    return { ok: true, sourceType: outPortTypeOf(s, edge.sourceHandle), targetType: "（结构连接）" };
  }

  const sType = outPortTypeOf(s, edge.sourceHandle);
  const tType = inPortTypeOf(t, edge.targetHandle);

  // 控制流：只能连接控制流。
  if (tType === "控制流") {
    if (sType === "控制流")
      return { ok: true, sourceType: sType, targetType: tType };
    return {
      ok: false,
      sourceType: sType,
      targetType: tType,
      reason: "分支/循环体端口只能连到 IF / FOREACH 的分支输出（控制流），不能连数据",
    };
  }
  if (sType === "控制流") {
    return {
      ok: false,
      sourceType: sType,
      targetType: tType,
      reason: "不能把控制流分支连到普通数据端口",
    };
  }

  // ★ 「输入键」端口（edge.targetHandle === "key"）：
  //   原来硬限只接受 input 节点，现在改为按字符串类型走正常兼容判定（PORT_TYPE["key"] = "字符串"）。
  //   字典运算（DICT_GET / DICT_HAS_KEY / DICT_APPEND）的 key 是字典键，语义上是字符串，
  //   不限于 input 节点——也可接 CONST 或其它输出字符串的节点。
  //   硬限删除后，走下方 typeAccepts("字符串", sType) 即可（对一切类型放行）。

  // 任意类型豁免（两端任一为任意/待定，不报错）。
  if (
    tType.includes("任意") ||
    sType.includes("任意") ||
    sType.includes("随输入") ||
    sType.includes("未知")
  ) {
    return { ok: true, sourceType: sType, targetType: tType };
  }

  if (!typeAccepts(tType, sType)) {
    return {
      ok: false,
      sourceType: sType,
      targetType: tType,
      reason: `类型不匹配：「${sType}」无法接入「${tType}」端口`,
    };
  }
  return { ok: true, sourceType: sType, targetType: tType };
}

export const ENTITY_TYPES = [
  "MATERIAL",
  "PART",
  "PRODUCT",
  "TECH_NODE",
  "WAREHOUSE",
  "PRODUCTION_LINE",
  "FUEL",
  "VEHICLE",
  "INFRASTRUCTURE",
  "MAP_NODE",
];

export const COMPARE_OPS = ["GTE", "LTE", "GT", "LT", "EQ"];

// ============ 中文显示名（仅用于可视化编辑器展示，不影响序列化枚举值） ============
export const ENTITY_TYPE_LABEL: Record<string, string> = {
  MATERIAL: "原料",
  PART: "零件",
  PRODUCT: "成品",
  TECH_NODE: "科技节点",
  WAREHOUSE: "仓库",
  PRODUCTION_LINE: "生产线",
  FUEL: "燃料",
  VEHICLE: "载具",
  INFRASTRUCTURE: "基建",
  MAP_NODE: "地图节点",
};

// 各实体的真实业务字段（供引擎按实体读取属性）。
// 编辑器按所选实体类型动态列出其字段并中文展示；value 存真实字段名。
export const ENTITY_FIELDS: Record<string, { key: string; label: string }[]> = {
  MATERIAL: [
    { key: "name", label: "名称" },
    { key: "origin", label: "产地" },
    { key: "price", label: "单价" },
    { key: "carbonEmissionCoefficient", label: "碳排放系数" },
    { key: "type", label: "类型" },
  ],
  PART: [{ key: "name", label: "名称" }],
  PRODUCT: [{ key: "name", label: "名称" }],
  TECH_NODE: [
    { key: "name", label: "名称" },
    { key: "description", label: "描述" },
    { key: "tier", label: "层级" },
    { key: "researchCost", label: "研发成本" },
  ],
  WAREHOUSE: [
    { key: "name", label: "名称" },
    { key: "capacity", label: "容量" },
    { key: "price", label: "价格" },
    { key: "type", label: "类型" },
  ],
  PRODUCTION_LINE: [
    { key: "name", label: "名称" },
    { key: "price", label: "价格" },
    { key: "laborCount", label: "用工数" },
    { key: "maxPerYear", label: "年最大产量" },
  ],
  FUEL: [
    { key: "name", label: "名称" },
    { key: "pricePerLiter", label: "每升价格" },
  ],
  VEHICLE: [
    { key: "name", label: "名称" },
    { key: "fuelConsumptionPerKm", label: "每公里油耗" },
    { key: "maxCargo", label: "最大载重" },
    { key: "price", label: "价格" },
    { key: "carbonEmission", label: "碳排放" },
  ],
  INFRASTRUCTURE: [
    { key: "name", label: "名称" },
    { key: "footprint", label: "占地面积" },
    { key: "employmentRateBonus", label: "就业率加成" },
    { key: "populationBonus", label: "人口加成" },
    { key: "highQualityPopulationBonus", label: "高质量人口加成" },
    { key: "price", label: "价格" },
    { key: "happinessIndexBonus", label: "幸福指数加成" },
    { key: "perCapitaIncomeBonus", label: "人均收入加成" },
    { key: "carbonReductionBonus", label: "碳减排加成" },
    { key: "activationPrice", label: "激活价格" },
  ],
  MAP_NODE: [
    { key: "name", label: "名称" },
    { key: "region", label: "区域" },
    { key: "x", label: "横坐标" },
    { key: "y", label: "纵坐标" },
  ],
};

export const VALUE_TYPE_LABEL: Record<string, string> = {
  ENTITY: "数据管理属性（数据源）", // 运行时从数据管理读取实体真实属性
  FIELD: "产业字段现值（数据源）", // 运行时读取某参与方公司当前的产业字段值
  INPUT: "输入项（输入源）", // 创建合同时由用户填写
  CONST: "常量",
  FORMULA: "公式",
  VAR: "变量",
  INDUSTRY_IS: "产业类型判断(布尔)",
};

// 合同只作用于产业字段，效果与检查种类均已收敛。
export const EFFECT_KIND_LABEL: Record<string, string> = {
  FIELD: "产业字段",
};

export const COND_KIND_LABEL: Record<string, string> = {
  VALUE_COMPARE: "数值互相比较",
  FIELD_COMPARE: "产业字段比较",
  INDUSTRY_IS: "产业类型是",
  DICT_COMPARE: "字典相互比较",
  LIST_COMPARE: "列表相互比较",
};

export const COMPARE_OP_LABEL: Record<string, string> = {
  GTE: "≥",
  LTE: "≤",
  GT: ">",
  LT: "<",
  EQ: "=",
  CONTAINS: "包含",
  HAS_KEY: "含键",
  ELEMENT_EQ: "元素相等",
  LEN_GTE: "长度≥",
  LEN_LTE: "长度≤",
  LEN_EQ: "长度=",
};

// 数值源(value)节点的输入端口 handle → 中文标签（按 handle 名映射，避免不同来源端口错位）。
export const INPUT_PORT_LABEL: Record<string, string> = {
  entityRef: "实体引用",
  multiplyByInput: "乘输入",
  key: "输入键",
  value: "常量",
  routeRef: "节点列表",
  fieldParty: "字段所属方",
  infraList: "基建列表",
  vehList: "载具列表",
};

// ============ 序列化：图 → 后端 JSON ============

function findEdge(graph: GGraph, targetId: string, handle: string): GEdge | undefined {
  return graph.edges.find((e) => e.target === targetId && e.targetHandle === handle);
}

function nodeById(graph: GGraph, id?: string): GNode | undefined {
  if (!id) return undefined;
  return graph.nodes.find((n) => n.id === id);
}

// 输入项节点 → INPUT ValueSpec：聚合口径由「连线的源端口(handle)」决定。
// 适用于两种连线形态：输入节点 → value 节点的 key 端口（经 resolveValueSpec），
// 以及输入节点 → 效果/运算/条件节点端口直连（经 resolveValueSource）——
// 此前直连路径不识别源端口，聚合端点（如「每种种类的仓库总存储量」）被当作
// 原始清单透传，下游拿到 {仓库名称: 数量} 而非 {仓库种类: 总储量}。
function buildInputSpec(graph: GGraph, edge?: GEdge): any {
  const inputNode = nodeById(graph, edge?.source);
  const spec: any = { type: "INPUT", key: inputNode?.data.key || "" };
  const h = edge?.sourceHandle;
  if (!h) return spec;
  // 原料清单输入源连自「碳排放合计」端口时，标记为 CARBON 聚合端点。
  if (h === "carbon") spec.aggregate = "CARBON";
  // 节点列表输入源连自「路程」端口时，标记为 ROUTE_DISTANCE 聚合端点。
  else if (h === "distance") spec.aggregate = "ROUTE_DISTANCE";
  // 节点列表输入源连自「存在的路径类型」端口时，标记为 ROUTE_PATH_TYPES 聚合端点。
  else if (h === "pathTypes") spec.aggregate = "ROUTE_PATH_TYPES";
  // 节点列表输入源连自「起始节点名」端口时，标记为 ROUTE_START_NODE_NAME 聚合端点。
  else if (h === "startNodeName") spec.aggregate = "ROUTE_START_NODE_NAME";
  // 节点列表输入源连自「终止节点名」端口时，标记为 ROUTE_END_NODE_NAME 聚合端点。
  else if (h === "endNodeName") spec.aggregate = "ROUTE_END_NODE_NAME";
  // 零件清单输入源连自「所需原料」端口时，标记为 PART_MATERIALS 聚合端点。
  else if (h === "materials") spec.aggregate = "PART_MATERIALS";
  // 零件清单输入源连自「所需原料总数量」端口时，标记为 PART_MATERIAL_TOTAL_QTY 聚合端点。
  else if (h === "partMaterialQty") spec.aggregate = "PART_MATERIAL_TOTAL_QTY";
  // 产品清单输入源连自「需要的零件」端口时，标记为 PRODUCT_PARTS 聚合端点。
  else if (h === "parts") spec.aggregate = "PRODUCT_PARTS";
  // 产品清单输入源连自「所需零件总数量」端口时，标记为 PRODUCT_PARTS_TOTAL_QTY 聚合端点。
  else if (h === "productPartQty") spec.aggregate = "PRODUCT_PARTS_TOTAL_QTY";
  // 零件清单/产品清单输入源连自「所需的科技节点」端口时，按输入项类型标记聚合端点。
  else if (h === "techNodes") {
    if (inputNode?.data.type === "productList") spec.aggregate = "PRODUCT_TECH_NODES";
    else spec.aggregate = "PART_TECH_NODES";
  } else if (h === "price") {
    // 原料清单输入源连自「原料总价格」端口时，标记为 PRICE 聚合端点；
    // 若输入节点连有参与方，把其角色写入 spec.party，运行期据此按所在地筛选地点价。
    spec.aggregate = "PRICE";
    const partyEdge = findEdge(graph, inputNode?.id || "", "party");
    const partyNode = nodeById(graph, partyEdge?.source);
    if (partyNode && partyNode.type === "party") spec.party = partyNode.data.role;
  }
  // 原料/零件/产品/燃料清单输入源连自「总数量」端口时，标记为对应 *_TOTAL_QTY 聚合端点。
  else if (h === "materialQty") spec.aggregate = "MATERIAL_TOTAL_QTY";
  else if (h === "partQty") spec.aggregate = "PART_TOTAL_QTY";
  else if (h === "productQty") spec.aggregate = "PRODUCT_TOTAL_QTY";
  else if (h === "fuelQty") spec.aggregate = "FUEL_TOTAL_QTY";
  // 燃料清单输入源连自「燃料总价格」端口时，标记为 FUEL_TOTAL_PRICE 聚合端点。
  else if (h === "fuelPrice") spec.aggregate = "FUEL_TOTAL_PRICE";
  // 载具清单输入源连自各聚合端口时，标记为对应 VEHICLE_* 聚合端点。
  else if (h === "vehiclePrice") spec.aggregate = "VEHICLE_TOTAL_PRICE";
  else if (h === "vehicleCargo") spec.aggregate = "VEHICLE_CARGO";
  else if (h === "vehicleFuelPerKm") spec.aggregate = "VEHICLE_FUEL_PER_KM";
  else if (h === "vehicleCarbon") spec.aggregate = "VEHICLE_CARBON";
  // 仓库清单输入源连自「每种种类的仓库总存储量」/「仓库总价格」端口时，标记为对应聚合端点。
  else if (h === "warehouseStorage") spec.aggregate = "WAREHOUSE_STORAGE";
  else if (h === "warehousePrice") spec.aggregate = "WAREHOUSE_TOTAL_PRICE";
  // 科技树节点输入源连自「前置节点」/「研发费用」端口时，标记为对应聚合端点。
  else if (h === "prerequisites") spec.aggregate = "TECH_PREREQUISITES";
  else if (h === "researchCost") spec.aggregate = "TECH_RESEARCH_COST";
  // 基建清单输入源连自各聚合端口时，标记为对应 INFRA_* 聚合端点。
  else if (h.startsWith("infra")) {
    const m: Record<string, string> = {
      infraPrice: "INFRA_PRICE",
      infraFootprint: "INFRA_FOOTPRINT",
      infraEmployment: "INFRA_EMPLOYMENT",
      infraPopulation: "INFRA_POPULATION",
      infraHighQuality: "INFRA_HIGHQUALITY",
      infraHappiness: "INFRA_HAPPINESS",
      infraIncome: "INFRA_INCOME",
      infraCarbon: "INFRA_CARBON",
      // 「基建启用总费用」端口：activationPrice × 数量 之和。
      infraActivationPrice: "INFRA_ACTIVATION_PRICE",
    };
    if (m[h]) spec.aggregate = m[h];
  }
  return spec;
}

// 由 value 节点解析出 ValueSpec（ENTITY/INPUT/CONST/FORMULA）。
function resolveValueSpec(graph: GGraph, valueNode?: GNode): any {
  if (!valueNode) return { type: "INPUT", key: "" };
  const d = valueNode.data;
  const vt = d.valueType || "INPUT";
  if (vt === "ENTITY") {
    const entityRefEdge = findEdge(graph, valueNode.id, "entityRef");
    const multEdge = findEdge(graph, valueNode.id, "multiplyByInput");
    const entityInput = nodeById(graph, entityRefEdge?.source);
    const multInput = nodeById(graph, multEdge?.source);
    return {
      type: "ENTITY",
      entityType: d.entityType || "MATERIAL",
      entityRef: entityInput?.data.key || "",
      attribute: d.attribute || "price",
      multiplyByInput: multInput?.data.key || undefined,
    };
  }
  if (vt === "INPUT") {
    // 聚合口径由「输入节点 → value 节点 key 端口」这条连线的源端口决定，
    // 与输入节点直连效果/运算/条件端口的路径共用同一映射（buildInputSpec）。
    return buildInputSpec(graph, findEdge(graph, valueNode.id, "key"));
  }
  if (vt === "CONST") {
    const valEdge = findEdge(graph, valueNode.id, "value");
    const inputNode = nodeById(graph, valEdge?.source);
    // 保留原始值（数字或 JSON 数组/对象字符串），由后端解析
    return {
      type: "CONST",
      value: inputNode?.data.default ?? d.value,
    };
  }
  if (vt === "VAR") {
    return { type: "VAR", name: d.varName || d.name || "" };
  }
  if (vt === "ROUTE") {
    const routeEdge = findEdge(graph, valueNode.id, "routeRef");
    const routeInput = nodeById(graph, routeEdge?.source);
    return { type: "ROUTE", routeRef: routeInput?.data.key || "" };
  }
  if (vt === "FIELD") {
    // 产业字段现值：参与方由「字段所属方」端口连线指定，字段 key 在节点属性里选
    const partyEdge = findEdge(graph, valueNode.id, "fieldParty");
    const partyNode = nodeById(graph, partyEdge?.source);
    return {
      type: "FIELD",
      party: partyNode?.data.role || d.party || "",
      fieldKey: d.fieldKey || "",
    };
  }
  if (vt === "INDUSTRY_IS") {
    // 产业类型判断（布尔值源）：参与方由「字段所属方」端口连线指定，industryTypeId 在节点属性里选
    const partyEdge = findEdge(graph, valueNode.id, "fieldParty");
    const partyNode = nodeById(graph, partyEdge?.source);
    return {
      type: "INDUSTRY_IS",
      party: partyNode?.data.role || d.party || "",
      industryTypeId: d.industryTypeId ?? null,
    };
  }
  // FORMULA
  return { type: "FORMULA", expr: d.expr || "" };
}

// 由任意"产出值"的节点（value / list-op / dict-op / input）解析出 ValueSpec。
// 用于效果/检查的值、金额、数量等端口，支持运算节点串联。
function resolveValueSource(graph: GGraph, node?: GNode, viaEdge?: GEdge): any {
  if (!node) return { type: "INPUT", key: "" };
  if (node.type === "list-op" || node.type === "dict-op" || node.type === "calc" || node.type === "compare")
    return opNodeToSpec(graph, node);
  if (node.type === "value") return resolveValueSpec(graph, node);
  // 输入节点直连：聚合口径由连线的源端口决定（如「每种种类的仓库总存储量」→ WAREHOUSE_STORAGE）。
  if (node.type === "input") return buildInputSpec(graph, viaEdge);
  // 参与方节点「公司名称」输出端口：运行时取该参与方所绑定公司的 name 字段。
  if (node.type === "party" && viaEdge?.sourceHandle === "companyName")
    return { type: "PARTY_COMPANY_NAME", party: node.data.role };
  return { type: "INPUT", key: "" };
}

// 结构化深比较（反序列化时按 cond 同构匹配 IF 节点；列表/字典亦可）。
export function deepEqual(a: any, b: any): boolean {
  if (a === b) return true;
  if (a == null || b == null) return a == null && b == null;
  if (typeof a !== typeof b) return false;
  if (typeof a !== "object") return a === b;
  if (Array.isArray(a) || Array.isArray(b)) {
    if (!Array.isArray(a) || !Array.isArray(b)) return false;
    if (a.length !== b.length) return false;
    return a.every((x: any, i: number) => deepEqual(x, b[i]));
  }
  const ka = Object.keys(a);
  const kb = Object.keys(b);
  if (ka.length !== kb.length) return false;
  return ka.every((k) => deepEqual(a[k], b[k]));
}

// ===== 前端创建表单用的轻量条件求值器（输入项挂在 IF 分支时实时显隐） =====
export interface FormConditionCtx {
  // 已填写的输入项值（按字段 key）
  inputs: Record<string, any>;
  // 参与方当前所绑公司的产业类型 id（用于 INDUSTRY_IS）；未绑定返回 undefined
  industryTypeOf?: (role?: string) => number | null | undefined;
  // 公司当前产业字段值（用于 FIELD，表单期可能未知）；未提供时按无法判定处理
  fieldValue?: (fieldKey: string, role?: string) => any;
}

// 真值判定（与引擎 isTruthy 对齐）：非 null、非空字符串、非零数字、非空数组/对象为真。
function isTruthyForm(v: any): boolean {
  if (v === null || v === undefined) return false;
  if (typeof v === "string") return v.length > 0;
  if (typeof v === "number") return v !== 0;
  if (typeof v === "boolean") return v;
  if (Array.isArray(v)) return v.length > 0;
  if (typeof v === "object") return Object.keys(v).length > 0;
  return true;
}

// 求一个值源的原始值（数字/字符串/布尔），无法判定返回 null。
function evalFormValue(spec: any, ctx: FormConditionCtx): any | null {
  if (!spec || typeof spec !== "object") return null;
  switch (spec.type) {
    case "INPUT": {
      const v = ctx.inputs[spec.key];
      return v === undefined ? null : v;
    }
    case "CONST":
      return spec.value;
    case "INDUSTRY_IS":
      return ctx.industryTypeOf ? ctx.industryTypeOf(spec.party) === spec.industryTypeId : null;
    case "FIELD":
      return ctx.fieldValue ? ctx.fieldValue(spec.fieldKey, spec.party) : null;
    case "OP": {
      const op = spec.op as string;
      const args = (spec.args || []).map((a: any) => evalFormValue(a, ctx));
      if (args.some((x: any) => x === null)) return null;
      // 比较类 OP：返回布尔；算术类 OP：返回数字；其它未知 OP：无法判定
      if (op === "CMP_EQ") return args[0] === args[1];
      if (op === "CMP_NE") return args[0] !== args[1];
      if (op === "CMP_GT") return Number(args[0]) > Number(args[1]);
      if (op === "CMP_LT") return Number(args[0]) < Number(args[1]);
      if (op === "CMP_GTE") return Number(args[0]) >= Number(args[1]);
      if (op === "CMP_LTE") return Number(args[0]) <= Number(args[1]);
      if (op === "ADD") return Number(args[0]) + Number(args[1]);
      if (op === "SUB") return Number(args[0]) - Number(args[1]);
      if (op === "MUL") return Number(args[0]) * Number(args[1]);
      if (op === "DIV") return Number(args[1]) !== 0 ? Number(args[0]) / Number(args[1]) : 0;
      if (op === "EXP") return Math.exp(Number(args[0]));
      if (op === "LOG") {
        const x = Number(args[0]);
        const base = Number(args[1]);
        return base > 0 ? Math.log(x) / Math.log(base) : Math.log(x); // 底数缺省 → 自然对数
      }
      if (op === "MIN") return Math.min(Number(args[0]), Number(args[1]));
      if (op === "MAX") return Math.max(Number(args[0]), Number(args[1]));
      return null;
    }
    // FORMULA / ENTITY / ROUTE 在表单期无法静态求值 → 无法判定
    default:
      return null;
  }
}

// 求 IF 条件布尔结果；无法判定（依赖未填字段/公司字段未知等）返回 null，调用方按 fail-open 显示。
export function evalFormCondition(spec: any, ctx: FormConditionCtx): boolean | null {
  const v = evalFormValue(spec, ctx);
  if (v === null) return null;
  return isTruthyForm(v);
}

// 由 list-op / dict-op 节点解析出 OP 类型的 ValueSpec（递归求值 args）。
function opNodeToSpec(graph: GGraph, node: GNode): any {
  const op = node.data.op as string;
  const handles = OP_ARG_SPECS[op] || [];
  const args = handles.map((h) => {
    const e = findEdge(graph, node.id, h);
    const src = nodeById(graph, e?.source);
    if (src) return resolveValueSource(graph, src, e);
    // 未连线：取该参数端口的字面量默认值（用户在属性面板填写）。
    // 必须包成 CONST spec：裸字符串会被引擎 eval_value_spec 当数值源 to_number("PART")→0，
    // 导致 DICT_GET 等字典/列表运算的字符串键失效（永远命中默认值）。
    const lit = node.data.argLiterals?.[h];
    if (lit != null && lit !== "") return { type: "CONST", value: lit };
    return { type: "CONST", value: 0 };
  });
  return { type: "OP", op, args };
}

function resolvePartyRole(graph: GGraph, effectNode: GNode): string | null {
  const e = findEdge(graph, effectNode.id, "party");
  const partyNode = nodeById(graph, e?.source);
  return partyNode?.data.role || null;
}

// 把任意形态（数组 / JSON 字符串）规范化为字符串数组；非法则返回 null。
function normalizeList(raw: any): string[] | null {
  if (Array.isArray(raw)) return raw.map((x) => String(x));
  if (typeof raw === "string") {
    const s = raw.trim();
    if (!s) return null;
    try {
      const v = JSON.parse(s);
      if (Array.isArray(v)) return v.map((x: any) => String(x));
    } catch {
      // 允许纯逗号分隔写法，如 "基建A,基建B"
      if (s.includes(",")) return s.split(",").map((x) => x.trim()).filter(Boolean);
    }
  }
  return null;
}

// 基建清单输入源的「基建列表」输入端口：连入一个基建名称数组后，
// 返回该数组（前端据此过滤下拉）。支持两种来源：
//  - CONST 数值源：data.value 为数组或 JSON 字符串；
//  - 列表类型输入项：default 为数组。
// 其它（列表运算 / 变量等运行期才确定的列表）无法静态解析，返回 undefined（前端展示全部基建）。
function resolveInfraListFilter(graph: GGraph, inputNode: GNode): string[] | undefined {
  const e = findEdge(graph, inputNode.id, "infraList");
  if (!e) return undefined;
  const src = nodeById(graph, e.source);
  if (!src) return undefined;
  if (src.type === "value" && (src.data.valueType || "INPUT") === "CONST") {
    const arr = normalizeList(src.data.value);
    return arr && arr.length ? arr : undefined;
  }
  if (src.type === "input" && src.data.type === "list" && Array.isArray(src.data.default)) {
    const arr = normalizeList(src.data.default);
    return arr && arr.length ? arr : undefined;
  }
  return undefined;
}

// 载具清单输入源的「载具列表」输入端口：连入一个载具名称数组后，
// 返回该数组（前端据此过滤下拉）。逻辑与 resolveInfraListFilter 完全对称，仅 handle 不同。
function resolveVehicleListFilter(graph: GGraph, inputNode: GNode): string[] | undefined {
  const e = findEdge(graph, inputNode.id, "vehList");
  if (!e) return undefined;
  const src = nodeById(graph, e.source);
  if (!src) return undefined;
  if (src.type === "value" && (src.data.valueType || "INPUT") === "CONST") {
    const arr = normalizeList(src.data.value);
    return arr && arr.length ? arr : undefined;
  }
  if (src.type === "input" && src.data.type === "list" && Array.isArray(src.data.default)) {
    const arr = normalizeList(src.data.default);
    return arr && arr.length ? arr : undefined;
  }
  return undefined;
}

export interface FlatContract {
  partyRoles: any[];
  inputSchema: any[];
  effects: any[];
  conditions: any[];
}

// 解析某节点是否挂在某个 IF 分支之下；返回 { when, cond } 或 undefined。
// 该节点需存在一条 parent 边，其 source 是 if 节点、sourceHandle 为 then/else。
// 输入项（前端表单显隐）与检查（引擎执行短路）共用此逻辑。
function resolveControlBranch(graph: GGraph, node: GNode): { when: string; cond: any } | undefined {
  const pe = graph.edges.find((e) => e.target === node.id && e.targetHandle === "parent");
  if (!pe) return undefined;
  const ifNode = nodeById(graph, pe.source);
  if (!ifNode || ifNode.type !== "if") return undefined;
  const port = pe.sourceHandle || "";
  if (port !== "then" && port !== "else") return undefined;
  const condNode = nodeById(graph, findEdge(graph, ifNode.id, "cond")?.source);
  return { when: port, cond: resolveValueSource(graph, condNode, findEdge(graph, ifNode.id, "cond")) };
}

// 解析某输入项是否挂在某个 IF 分支之下（创建表单条件显隐）。
function resolveInputBranch(graph: GGraph, inputNode: GNode): { when: string; cond: any } | undefined {
  return resolveControlBranch(graph, inputNode);
}

export function graphToFlat(graph: GGraph): FlatContract {
  const parties = graph.nodes
    .filter((n) => n.type === "party")
    .map((n) => ({
      role: n.data.role,
      label: n.data.label,
      isHost: !!n.data.isHost,
      selectable: n.data.selectable !== false,
      // 限定该角色只能由指定产业类型的公司担任（不填=不限）
      industryTypeId: n.data.industryTypeId ?? undefined,
    }));

  const inputs = graph.nodes
    .filter((n) => n.type === "input")
    .map((n) => {
      // 解析该输入项是否挂在某个 IF 分支之下（条件显隐）。
      const br = resolveInputBranch(graph, n);
      const item: any = {
        key: n.data.key,
        label: n.data.label,
        type: n.data.type || "number",
        entityType: n.data.entityType,
        required: !!n.data.required,
        default: n.data.default,
        // 原料清单/零件清单等输入源若连了参与方，记下其角色，
        // 运行期据此按该参与方所在地节点过滤可选原料、并按地区取价。
        party: resolvePartyRole(graph, n),
        // 基建清单输入源若连了「基建列表」输入端口，记下受限的基建名数组，
        // 运行期（创建合同时）前端据此过滤基建下拉，未连则字段不存在、展示全部基建。
        allowedInfrastructures: resolveInfraListFilter(graph, n),
        // 载具清单输入源若连了「载具列表」输入端口，记下受限的载具名数组，
        // 运行期（创建合同时）前端据此过滤载具下拉，未连则字段不存在、展示全部载具。
        allowedVehicles: resolveVehicleListFilter(graph, n),
      };
      // 挂在 IF 分支下的输入项：记下分支归属（when + 条件值源），前端创建表单据此显隐。
      if (br) item.branch = { when: br.when, cond: br.cond };
      return item;
    });

  // 效果树：支持 IF/FOREACH/ASSIGN 嵌套。先找出所有效果节点及其"上级"关系。
  const effectNodes = graph.nodes.filter(
    (n) => n.type === "effect" || n.type === "if" || n.type === "foreach" || n.type === "assign",
  );
  const childParent = new Map<string, { parentId: string; port: string }>();
  for (const e of graph.edges) {
    if (e.targetHandle === "parent")
      childParent.set(e.target, { parentId: e.source, port: e.sourceHandle || "" });
  }
  const byId = (id?: string) => nodeById(graph, id);

  function effectSpec(node: GNode): any {
    const d = node.data;
    if (node.type === "if") {
      const condEdge = findEdge(graph, node.id, "cond");
      const condNode = byId(condEdge?.source);
      const thenNodes = effectNodes.filter(
        (n) =>
          childParent.get(n.id)?.parentId === node.id && childParent.get(n.id)?.port === "then",
      );
      const elseNodes = effectNodes.filter(
        (n) =>
          childParent.get(n.id)?.parentId === node.id && childParent.get(n.id)?.port === "else",
      );
      return {
        kind: "IF",
        cond: resolveValueSource(graph, condNode, condEdge),
        then: thenNodes.map(effectSpec),
        else: elseNodes.map(effectSpec),
      };
    }
    if (node.type === "foreach") {
      const itemsEdge = findEdge(graph, node.id, "items");
      const itemsNode = byId(itemsEdge?.source);
      const bodyNodes = effectNodes.filter(
        (n) =>
          childParent.get(n.id)?.parentId === node.id && childParent.get(n.id)?.port === "body",
      );
      return {
        kind: "FOREACH",
        items: resolveValueSource(graph, itemsNode, itemsEdge),
        var: d.var || "item",
        body: bodyNodes.map(effectSpec),
      };
    }
    if (node.type === "assign") {
      const vEdge = findEdge(graph, node.id, "value");
      const vNode = byId(vEdge?.source);
      return { kind: "ASSIGN", name: d.name || "", value: resolveValueSource(graph, vNode, vEdge) };
    }
    // 叶子效果：只有产业字段(FIELD)。以 fieldKey 定位，按公司所属产业类型解析到具体字段。
    // 可选第二数值来源 value2 + 组合方式 valueOp：最终写入量 = value <valueOp> value2。
    const party = resolvePartyRole(graph, node);
    const vEdge = findEdge(graph, node.id, "value");
    const vNode = byId(vEdge?.source);
    const v2Edge = findEdge(graph, node.id, "value2");
    const v2Node = byId(v2Edge?.source);
    return {
      kind: "FIELD",
      party,
      fieldKey: d.fieldKey || "",
      op: d.op || "ADD",
      valueOp: d.valueOp || undefined,
      value: resolveValueSource(graph, vNode, vEdge),
      value2: v2Node ? resolveValueSource(graph, v2Node, v2Edge) : undefined,
    };
  }

  const rootEffects = effectNodes
    .filter((n) => !childParent.has(n.id))
    .sort((a, b) => a.y - b.y || a.x - b.x);
  const effects = rootEffects.map(effectSpec);

  const conditions = graph.nodes
    .filter((n) => n.type === "condition")
    .map((n) => {
      const d = n.data;
      const kind = d.condKind as string;
      const base: any = { kind, label: d.label || "", errorMessage: d.errorMessage || "" };
      if (kind === "INDUSTRY_IS") {
        base.party = resolvePartyRole(graph, n);
        base.industryTypeId = d.industryTypeId ?? null;
      } else if (kind === "VALUE_COMPARE") {
        // 两个自由数值源互相比较，都比某参与方字段更灵活。
        const e1 = findEdge(graph, n.id, "value1");
        const e2 = findEdge(graph, n.id, "value2");
        base.op = d.op || "GTE";
        base.value1 = resolveValueSource(graph, nodeById(graph, e1?.source), e1);
        base.value2 = resolveValueSource(graph, nodeById(graph, e2?.source), e2);
      } else if (kind === "DICT_COMPARE") {
        // 两个自由字典源互相比较：前提（值二键 ⊆ 值一键，如需求键都在库存中）由引擎执行时校验；
        // 满足后对值二的每个键逐一比较。无需参与方。
        const e1 = findEdge(graph, n.id, "value1");
        const e2 = findEdge(graph, n.id, "value2");
        base.op = d.op || "GTE";
        base.value1 = resolveValueSource(graph, nodeById(graph, e1?.source), e1);
        base.value2 = resolveValueSource(graph, nodeById(graph, e2?.source), e2);
      } else if (kind === "LIST_COMPARE") {
        // 两个自由列表源互相比较：op∈{ELEMENT_EQ,CONTAINS,GT,GTE,EQ}，无需参与方。
        const e1 = findEdge(graph, n.id, "value1");
        const e2 = findEdge(graph, n.id, "value2");
        base.op = d.op || "GTE";
        base.value1 = resolveValueSource(graph, nodeById(graph, e1?.source), e1);
        base.value2 = resolveValueSource(graph, nodeById(graph, e2?.source), e2);
        } else {
        // 兼容旧版 FIELD_COMPARE：左操作数为某参与方字段 + 右侧值。
        const vEdge = findEdge(graph, n.id, "value");
        const vNode = nodeById(graph, vEdge?.source);
        base.kind = "FIELD_COMPARE";
        base.party = resolvePartyRole(graph, n);
        base.fieldKey = d.fieldKey || "";
        base.op = d.op || "GTE";
        base.value = resolveValueSource(graph, vNode, vEdge);
      }
      // 挂在 IF 分支下的检查：记下分支归属（when + 条件值源），引擎 runConditions 据此短路。
      const br = resolveControlBranch(graph, n);
      if (br) base.branch = { when: br.when, cond: br.cond };
      return base;
    });

  return { partyRoles: parties, inputSchema: inputs, effects, conditions };
}

// ============ 反序列化：后端 JSON → 图 ============

let _seq = 0;
function uid(prefix = "n"): string {
  _seq += 1;
  return `${prefix}_${Date.now().toString(36)}_${_seq}`;
}

export function flatToGraph(flat: Partial<FlatContract>): GGraph {
  const nodes: GNode[] = [];
  const edges: GEdge[] = [];
  // 记录已构建的 IF 节点（含其 cond 值源），用于反序列化时把输入项的 branch 还原成 parent 连线。
  const builtIfNodes: { node: GNode; cond: any }[] = [];
  // 记录带 branch 的输入项节点，待 IF 节点建好后按 cond 同构匹配并连线。
  const branchedInputs: { node: GNode; when: string; cond: any }[] = [];
  // 记录带 branch 的检查节点，待 IF 节点建好后按 cond 同构匹配并连线（IF.then/else → condition.parent）。
  const branchedConditions: { node: GNode; when: string; cond: any }[] = [];
  const colW = 240;
  // 聚合端点枚举 → 输入节点输出端口 handle（反序列化时把 spec.aggregate 还原成对应连线端口，
  // 否则 materials/parts/techNodes 等聚合端口在保存后会被错连到普通 out 端口而丢失语义）。
  const AGGREGATE_TO_HANDLE: Record<string, string> = {
    CARBON: "carbon",
    ROUTE_DISTANCE: "distance",
    ROUTE_PATH_TYPES: "pathTypes",
    ROUTE_START_NODE_NAME: "startNodeName",
    ROUTE_END_NODE_NAME: "endNodeName",
    PART_MATERIALS: "materials",
    PART_MATERIAL_TOTAL_QTY: "partMaterialQty",
    PRODUCT_PARTS: "parts",
    PRODUCT_PARTS_TOTAL_QTY: "productPartQty",
    PRICE: "price",
    INFRA_PRICE: "infraPrice",
    INFRA_FOOTPRINT: "infraFootprint",
    INFRA_EMPLOYMENT: "infraEmployment",
    INFRA_POPULATION: "infraPopulation",
    INFRA_HIGHQUALITY: "infraHighQuality",
    INFRA_HAPPINESS: "infraHappiness",
    INFRA_INCOME: "infraIncome",
    INFRA_CARBON: "infraCarbon",
    INFRA_ACTIVATION_PRICE: "infraActivationPrice",
    TECH_PREREQUISITES: "prerequisites",
    TECH_RESEARCH_COST: "researchCost",
    PART_TECH_NODES: "techNodes",
    PRODUCT_TECH_NODES: "techNodes",
    MATERIAL_TOTAL_QTY: "materialQty",
    PART_TOTAL_QTY: "partQty",
    PRODUCT_TOTAL_QTY: "productQty",
    FUEL_TOTAL_QTY: "fuelQty",
    FUEL_TOTAL_PRICE: "fuelPrice",
    VEHICLE_TOTAL_PRICE: "vehiclePrice",
    VEHICLE_CARGO: "vehicleCargo",
    VEHICLE_FUEL_PER_KM: "vehicleFuelPerKm",
    VEHICLE_CARBON: "vehicleCarbon",
    WAREHOUSE_STORAGE: "warehouseStorage",
    WAREHOUSE_TOTAL_PRICE: "warehousePrice",
  };
  const rowH = 130;
  const PORT_GAP = 22; // 仅在反序列化布局用，与编辑器 PORT_GAP 保持一致

  // 根节点
  const root: GNode = { id: uid("root"), type: "root", x: 20, y: 20, data: {} };
  nodes.push(root);

  // 参与方
  const partyNodes: GNode[] = [];
  (flat.partyRoles || []).forEach((p: any, i: number) => {
    const n: GNode = {
      id: uid("party"),
      type: "party",
      x: 20,
      y: 80 + i * rowH,
      data: {
        role: p.role,
        label: p.label,
        isHost: !!p.isHost,
        selectable: p.selectable !== false,
        industryTypeId: p.industryTypeId ?? undefined,
      },
    };
    nodes.push(n);
    partyNodes.push(n);
    edges.push({
      id: uid("e"),
      source: root.id,
      sourceHandle: "out",
      target: n.id,
      targetHandle: "in",
    });
  });
  const partyByRole = new Map(partyNodes.map((n) => [n.data.role, n]));

  // 输入项
  const inputNodes: GNode[] = [];
  (flat.inputSchema || []).forEach((f: any, i: number) => {
    const n: GNode = {
      id: uid("input"),
      type: "input",
      x: 20 + colW,
      y: 80 + i * rowH,
      data: {
        key: f.key,
        label: f.label,
        type: f.type || "number",
        entityType: f.entityType,
        required: !!f.required,
        default: f.default,
      },
    };
    nodes.push(n);
    inputNodes.push(n);
    edges.push({
      id: uid("e"),
      source: root.id,
      sourceHandle: "out",
      target: n.id,
      targetHandle: "in",
    });
    // 反序列化：若该输入项挂在某个 IF 分支之下，记录待连；待 IF 节点建好后按 cond 同构匹配连线。
    if (f.branch && (f.branch.when === "then" || f.branch.when === "else")) {
      branchedInputs.push({ node: n, when: f.branch.when, cond: f.branch.cond });
    }
    // 反序列化：若该输入源在序列化时记录了连的参与方角色，恢复 party 连接。
    if (f.party && partyByRole.has(f.party)) {
      edges.push({
        id: uid("e"),
        source: partyByRole.get(f.party)!.id,
        sourceHandle: "out",
        target: n.id,
        targetHandle: "party",
      });
    }
    // 反序列化：基建清单若记录了 allowedInfrastructures（受限基建名数组），
    // 还原一个 CONST 值节点并连到「基建列表」输入端口，使前端下拉仅展示这些基建。
    if (
      (f.type === "infrastructureList" || f.entityType === "INFRASTRUCTURE") &&
      Array.isArray(f.allowedInfrastructures) &&
      f.allowedInfrastructures.length
    ) {
      const cn: GNode = {
        id: uid("value"),
        type: "value",
        x: 20 + colW - 220,
        y: 80 + i * rowH,
        data: { valueType: "CONST", value: JSON.stringify(f.allowedInfrastructures) },
      };
      nodes.push(cn);
      edges.push({
        id: uid("e"),
        source: cn.id,
        sourceHandle: "out",
        target: n.id,
        targetHandle: "infraList",
      });
    }
    // 反序列化：载具清单若记录了 allowedVehicles（受限载具名数组），
    // 还原一个 CONST 值节点并连到「载具列表」输入端口，使前端下拉仅展示这些载具。
    if (
      (f.type === "vehicleList" || f.entityType === "VEHICLE") &&
      Array.isArray(f.allowedVehicles) &&
      f.allowedVehicles.length
    ) {
      const cn: GNode = {
        id: uid("value"),
        type: "value",
        x: 20 + colW - 220,
        y: 80 + i * rowH,
        data: { valueType: "CONST", value: JSON.stringify(f.allowedVehicles) },
      };
      nodes.push(cn);
      edges.push({
        id: uid("e"),
        source: cn.id,
        sourceHandle: "out",
        target: n.id,
        targetHandle: "vehList",
      });
    }
  });
  const inputByKey = new Map(inputNodes.map((n) => [n.data.key, n]));

  // 创建一个 value 节点并连接其输入（ENTITY/INPUT/CONST/FORMULA）。
  function buildValueNode(
    spec: any,
    ownerId: string,
    targetHandle: string,
    x: number,
    y: number,
  ): GNode | undefined {
    if (!spec) return undefined;
    const vn: GNode = {
      id: uid("value"),
      type: "value",
      x,
      y,
      data: {
        valueType: spec.type || "INPUT",
        entityType: spec.entityType,
        attribute: spec.attribute,
        routeRef: spec.routeRef,
        expr: spec.expr,
        value: spec.value,
        varName: spec.name,
        party: spec.party,
        fieldKey: spec.fieldKey,
        industryTypeId: spec.industryTypeId,
      },
    };
    nodes.push(vn);
    if (spec.type === "ENTITY") {
      const inp = inputByKey.get(spec.entityRef);
      if (inp)
        edges.push({
          id: uid("e"),
          source: inp.id,
          target: vn.id,
          targetHandle: "entityRef",
        });
      if (spec.multiplyByInput) {
        const m = inputByKey.get(spec.multiplyByInput);
        if (m)
          edges.push({
            id: uid("e"),
            source: m.id,
            target: vn.id,
            targetHandle: "multiplyByInput",
          });
      }
    } else if (spec.type === "INPUT") {
      const inp = inputByKey.get(spec.key);
      if (inp)
        edges.push({
          id: uid("e"),
          source: inp.id,
          // 若该输入值连的是某个聚合端点（如 materials/parts/techNodes），
          // 还原连线到对应输出端口；否则连普通 out 端口。
          sourceHandle: AGGREGATE_TO_HANDLE[(spec as any).aggregate],
          target: vn.id,
          targetHandle: "key",
        });
    } else if (spec.type === "ROUTE") {
      const inp = inputByKey.get(spec.routeRef);
      if (inp)
        edges.push({
          id: uid("e"),
          source: inp.id,
          target: vn.id,
          targetHandle: "routeRef",
        });
    } else if (spec.type === "FIELD") {
      const pn = partyByRole.get(spec.party);
      if (pn)
        edges.push({
          id: uid("e"),
          source: pn.id,
          target: vn.id,
          targetHandle: "fieldParty",
        });
    } else if (spec.type === "INDUSTRY_IS") {
      const pn = partyByRole.get(spec.party);
      if (pn)
        edges.push({
          id: uid("e"),
          source: pn.id,
          target: vn.id,
          targetHandle: "fieldParty",
        });
    }
    edges.push({
      id: uid("e"),
      source: vn.id,
      sourceHandle: "out",
      target: ownerId,
      targetHandle,
    });
    return vn;
  }

  // 通用：依据 spec.type 派发到 value 或 op 节点。
  function buildValueAny(
    spec: any,
    ownerId: string,
    targetHandle: string,
    x: number,
    y: number,
  ): GNode | undefined {
    if (!spec) return undefined;
    // PARTY_COMPANY_NAME：直接从参与方节点的「公司名称」输出端口连线，不创建中间 value 节点。
    if (spec.type === "PARTY_COMPANY_NAME") {
      const pn = partyByRole.get(spec.party);
      if (pn)
        edges.push({
          id: uid("e"),
          source: pn.id,
          sourceHandle: "companyName",
          target: ownerId,
          targetHandle,
        });
      return pn;
    }
    if (spec.type === "OP") return buildOpNode(spec, ownerId, targetHandle, x, y);
    return buildValueNode(spec, ownerId, targetHandle, x, y);
  }

  // 由 OP 类型的 spec 还原出 list-op / dict-op 节点，并递归还原其参数。
  function buildOpNode(
    spec: any,
    ownerId: string,
    targetHandle: string,
    x: number,
    y: number,
  ): GNode | undefined {
    if (!spec || spec.type !== "OP") return undefined;
    const op = spec.op as string;
    const cat = opCategory(op);
    const type: GNodeType = cat === "dict" ? "dict-op" : cat === "arith" ? "calc" : "list-op";
    const on: GNode = {
      id: uid(type),
      type,
      x,
      y,
      data: { op, argLiterals: {} },
    };
    nodes.push(on);
    const handles = OP_ARG_SPECS[op] || [];
    (spec.args || []).forEach((arg: any, i: number) => {
      const h = handles[i];
      if (!h) return;
      buildValueAny(arg, on.id, h, x + colW, y + i * PORT_GAP);
    });
    edges.push({
      id: uid("e"),
      source: on.id,
      sourceHandle: "out",
      target: ownerId,
      targetHandle,
    });
    return on;
  }

  // 效果（递归：支持 IF / FOREACH / ASSIGN 嵌套）。
  function buildEffectNode(
    eff: any,
    x: number,
    y: number,
    parentId?: string,
    parentPort?: string,
  ): GNode {
    const type: GNodeType =
      eff.kind === "IF"
        ? "if"
        : eff.kind === "FOREACH"
          ? "foreach"
          : eff.kind === "ASSIGN"
            ? "assign"
            : "effect";
    const data: Record<string, any> = { effectKind: eff.kind };
    if (type === "effect") {
      data.effectKind = "FIELD";
      data.op = eff.op || "ADD";
      data.fieldKey = eff.fieldKey || "";
      data.valueOp = eff.valueOp || undefined;
    } else if (type === "foreach") {
      data.var = eff.var || "item";
    } else if (type === "assign") {
      data.name = eff.name || "";
    }
    const en: GNode = { id: uid(type), type, x, y, data };
    nodes.push(en);
    if (parentId && parentPort) {
      edges.push({
        id: uid("e"),
        source: parentId,
        sourceHandle: parentPort,
        target: en.id,
        targetHandle: "parent",
      });
    }
    if (type === "effect") {
      const party = partyByRole.get(eff.party);
      if (party)
        edges.push({ id: uid("e"), source: party.id, target: en.id, targetHandle: "party" });
      buildValueAny(eff.value, en.id, "value", x + colW, y);
      if (eff.value2) buildValueAny(eff.value2, en.id, "value2", x + colW, y + PORT_GAP);
    } else if (type === "if") {
      builtIfNodes.push({ node: en, cond: eff.cond });
      buildValueAny(eff.cond, en.id, "cond", x + colW, y);
      const thenLen = (eff.then || []).length;
      (eff.then || []).forEach((sub: any, i: number) =>
        buildEffectNode(sub, x + colW * 2, y + i * rowH, en.id, "then"),
      );
      (eff.else || []).forEach((sub: any, i: number) =>
        buildEffectNode(sub, x + colW * 2, y + thenLen * rowH + i * rowH, en.id, "else"),
      );
    } else if (type === "foreach") {
      buildValueAny(eff.items, en.id, "items", x + colW, y);
      (eff.body || []).forEach((sub: any, i: number) =>
        buildEffectNode(sub, x + colW * 2, y + i * rowH, en.id, "body"),
      );
    } else if (type === "assign") {
      buildValueAny(eff.value, en.id, "value", x + colW, y);
    }
    return en;
  }

  (flat.effects || []).forEach((eff: any, i: number) => {
    buildEffectNode(eff, 20 + colW * 2, 80 + i * rowH);
  });

  // 反序列化：把带 branch 的输入项还原成 IF 分支的 parent 连线。
  // 按 cond 值源同构匹配（deepEqual），找到对应 IF 节点后连 then/else → 输入.parent。
  for (const bi of branchedInputs) {
    const match = builtIfNodes.find((x) => deepEqual(x.cond, bi.cond));
    if (match) {
      edges.push({
        id: uid("e"),
        source: match.node.id,
        sourceHandle: bi.when,
        target: bi.node.id,
        targetHandle: "parent",
      });
    }
  }

  // 检查
  (flat.conditions || []).forEach((c: any, i: number) => {
    const kind: string =
      c.kind === "INDUSTRY_IS"
        ? "INDUSTRY_IS"
        : c.kind === "VALUE_COMPARE"
          ? "VALUE_COMPARE"
      : c.kind === "DICT_COMPARE"
        ? "DICT_COMPARE"
        : c.kind === "LIST_COMPARE"
          ? "LIST_COMPARE"
          : "FIELD_COMPARE";
    const cn: GNode = {
      id: uid("condition"),
      type: "condition",
      x: 20 + colW * 2,
      y: 80 + (flat.effects?.length || 0) * rowH + i * rowH,
      data: {
        condKind: kind,
        label: c.label,
        errorMessage: c.errorMessage || "",
        op: c.op || "GTE",
        fieldKey: c.fieldKey || "",
        industryTypeId: c.industryTypeId ?? undefined,
      },
    };
    nodes.push(cn);
    // 反序列化：若该检查在序列化时记录了所属 IF 分支，收集待连线。
    if (c.branch && (c.branch.when === "then" || c.branch.when === "else")) {
      branchedConditions.push({ node: cn, when: c.branch.when, cond: c.branch.cond });
    }
    if (kind === "INDUSTRY_IS") {
      const party = partyByRole.get(c.party);
      if (party)
        edges.push({
          id: uid("e"),
          source: party.id,
          target: cn.id,
          targetHandle: "party",
        });
    } else if (kind === "FIELD_COMPARE") {
      const party = partyByRole.get(c.party);
      if (party)
        edges.push({
          id: uid("e"),
          source: party.id,
          target: cn.id,
          targetHandle: "party",
        });
      buildValueAny(c.value, cn.id, "value", 20 + colW * 3, cn.y);
    } else {
      // VALUE_COMPARE / DICT_COMPARE：两个自由数值源（字典互相比较同样用值1/值2 端口）
      buildValueAny(c.value1, cn.id, "value1", 20 + colW * 3, cn.y);
      buildValueAny(c.value2, cn.id, "value2", 20 + colW * 3, cn.y + PORT_GAP);
    }
  });

  // 反序列化：把带 branch 的检查还原成 IF 分支的 parent 连线（与输入项同构匹配逻辑一致）。
  for (const bc of branchedConditions) {
    const match = builtIfNodes.find((x) => deepEqual(x.cond, bc.cond));
    if (match) {
      edges.push({
        id: uid("e"),
        source: match.node.id,
        sourceHandle: bc.when,
        target: bc.node.id,
        targetHandle: "parent",
      });
    }
  }

  return { nodes, edges };
}
