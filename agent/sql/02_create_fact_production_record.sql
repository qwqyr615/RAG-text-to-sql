-- 02_create_fact_production_record.sql
--
-- 服务层宽表：显式 DDL（类型 / 主键 / 可空性 / COMMENT / 索引全部写死）。
--
-- 与旧实现的区别：
--   旧实现是 CREATE TABLE fact_production_record LIKE intelligent_production_iiot，
--   把源表的结构（含 17 个 TEXT 类型列，其中 11 个其实是数值指标）原样继承下来。
--   TEXT 存储数值会导致 ORDER BY / MIN / MAX 按字典序计算，
--   例如 ORDER BY downtime_minutes DESC 会把 9.99 排在 25.08 前面。
--
-- 书写约定（metadata/schema_ddl.py 会解析本文件，请保持一致）：
--   每个列一行：列名 + 类型 + NOT NULL/NULL + COMMENT '说明'
--   COMMENT 中不要出现英文单引号与英文分号
--
-- 类型选择规则：
--   编码/枚举      -> VARCHAR
--   主键与计数     -> INT
--   金额           -> DECIMAL（避免浮点误差）
--   测量值/比率/得分 -> DOUBLE

DROP TABLE IF EXISTS fact_production_record;

CREATE TABLE fact_production_record (
  record_id                 INT           NOT NULL COMMENT '生产记录主键，一行代表一次生产运行',

  machine_id                VARCHAR(50)   NOT NULL COMMENT '设备编码，例如 M01',
  production_line           VARCHAR(50)   NOT NULL COMMENT '生产线编码，例如 Line_A',
  batch_id                  VARCHAR(50)   NOT NULL COMMENT '生产批次编码，例如 B0001',
  shift                     VARCHAR(20)   NOT NULL COMMENT '生产班次，Morning / Afternoon / Night',
  product_type               VARCHAR(50)   NOT NULL COMMENT '产品类型编码，例如 Product_A',

  ambient_temperature       DOUBLE        NULL COMMENT '环境温度，单位摄氏度',
  humidity                  DOUBLE        NULL COMMENT '环境湿度百分比',
  air_pressure              DOUBLE        NULL COMMENT '气压',
  ambient_vibration         DOUBLE        NOT NULL COMMENT '环境振动值',
  motor_temperature         DOUBLE        NULL COMMENT '电机温度，单位摄氏度',
  spindle_speed             DOUBLE        NULL COMMENT '主轴转速',
  motor_current             DOUBLE        NULL COMMENT '电机电流',
  torque                    DOUBLE        NULL COMMENT '扭矩',
  vibration                 DOUBLE        NULL COMMENT '振动值',
  acoustic_level            DOUBLE        NULL COMMENT '噪声水平',
  bearing_temperature       DOUBLE        NULL COMMENT '轴承温度，单位摄氏度',
  process_temperature       DOUBLE        NULL COMMENT '工艺温度，单位摄氏度',
  hydraulic_pressure        DOUBLE        NULL COMMENT '液压压力',
  flow_rate                 DOUBLE        NULL COMMENT '流量',
  coolant_temperature       DOUBLE        NULL COMMENT '冷却液温度，单位摄氏度',
  material_feed_rate        DOUBLE        NULL COMMENT '进料速率',
  feed_pressure             DOUBLE        NULL COMMENT '进给压力',

  cycle_time                DOUBLE        NOT NULL COMMENT '生产节拍，单位秒',
  throughput_rate           DOUBLE        NOT NULL COMMENT '吞吐率',
  production_volume         DOUBLE        NOT NULL COMMENT '产量',
  machine_utilization       DOUBLE        NOT NULL COMMENT '设备利用率百分比',
  resource_utilization      DOUBLE        NOT NULL COMMENT '资源利用率百分比',
  operator_load             DOUBLE        NOT NULL COMMENT '人员负荷',
  power_consumption         DOUBLE        NOT NULL COMMENT '能耗',
  energy_per_unit           DOUBLE        NOT NULL COMMENT '单位能耗',

  defect_rate               DOUBLE        NOT NULL COMMENT '缺陷率百分比，越高越差，口径 AVG(defect_rate)',
  quality_score             DOUBLE        NOT NULL COMMENT '质量得分，越高越好，口径 AVG(quality_score)',
  first_pass_yield          DOUBLE        NOT NULL COMMENT '良率或直通率百分比，越高越好，口径 AVG(first_pass_yield)',
  fault_event_count         INT           NOT NULL COMMENT '故障事件次数，口径 SUM(fault_event_count)',
  downtime_minutes          DOUBLE        NOT NULL COMMENT '停机时长，单位分钟，口径 SUM 或 AVG(downtime_minutes)',
  maintenance_frequency     DOUBLE        NOT NULL COMMENT '维护频次',
  production_cost_per_unit  DECIMAL(12,4) NOT NULL COMMENT '单位生产成本，金额类用 DECIMAL 避免浮点误差',

  resource_efficiency       DOUBLE        NOT NULL COMMENT '资源效率百分比',
  production_efficiency     DOUBLE        NOT NULL COMMENT '生产效率百分比',
  energy_saving_pct         DOUBLE        NOT NULL COMMENT '节能比例百分比',
  downtime_reduction_pct    DOUBLE        NOT NULL COMMENT '停机下降比例百分比',
  cost_reduction_pct        DOUBLE        NOT NULL COMMENT '成本下降比例百分比',
  benefit_score             DOUBLE        NOT NULL COMMENT '综合效益得分',

  operation_mode            VARCHAR(64)   NOT NULL COMMENT '生产模式，例如 Balanced_Production / Precision_Quality',

  PRIMARY KEY (record_id),
  KEY idx_machine_id (machine_id),
  KEY idx_production_line (production_line),
  KEY idx_product_type (product_type),
  KEY idx_batch_id (batch_id),
  KEY idx_shift (shift)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4
  COMMENT='生产记录事实宽表，单表模型的服务层，一行代表一次生产运行，含维度编码与全部质量设备产量指标';
