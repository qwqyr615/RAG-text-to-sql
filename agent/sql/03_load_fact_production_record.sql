-- 03_load_fact_production_record.sql
--
-- 幂等装载：TRUNCATE + 全量重灌。原始层 intelligent_production_iiot 只读、不修改。
--
-- 与旧实现的区别：
--   旧实现是 INSERT IGNORE INTO fact_production_record SELECT * FROM 源表，
--   一是 SELECT * 会让上游加列或改列序静默改变本表结构，
--   二是不会纠正类型。这里改为显式列清单 + 显式 CAST。
--
-- 本次治理的核心：下面 11 个数值指标原本以 TEXT 存储，在此显式转换为数值类型。
--   first_pass_yield / downtime_minutes / maintenance_frequency /
--   production_cost_per_unit / resource_efficiency / production_efficiency /
--   energy_saving_pct / downtime_reduction_pct / cost_reduction_pct / benefit_score
--   -> CAST 为 DOUBLE（production_cost_per_unit 为 DECIMAL）
--   fault_event_count -> CAST 为 SIGNED 即整数
-- 已核对源数据：这 11 列没有空值、没有非数字脏值，转换不会产生静默归零。

TRUNCATE TABLE fact_production_record;

INSERT INTO fact_production_record (
  record_id,
  machine_id,
  production_line,
  batch_id,
  shift,
  product_type,
  ambient_temperature,
  humidity,
  air_pressure,
  ambient_vibration,
  motor_temperature,
  spindle_speed,
  motor_current,
  torque,
  vibration,
  acoustic_level,
  bearing_temperature,
  process_temperature,
  hydraulic_pressure,
  flow_rate,
  coolant_temperature,
  material_feed_rate,
  feed_pressure,
  cycle_time,
  throughput_rate,
  production_volume,
  machine_utilization,
  resource_utilization,
  operator_load,
  power_consumption,
  energy_per_unit,
  defect_rate,
  quality_score,
  first_pass_yield,
  fault_event_count,
  downtime_minutes,
  maintenance_frequency,
  production_cost_per_unit,
  resource_efficiency,
  production_efficiency,
  energy_saving_pct,
  downtime_reduction_pct,
  cost_reduction_pct,
  benefit_score,
  operation_mode
)
SELECT
  record_id,
  TRIM(machine_id),
  TRIM(production_line),
  TRIM(batch_id),
  TRIM(shift),
  TRIM(product_type),
  ambient_temperature,
  humidity,
  air_pressure,
  ambient_vibration,
  motor_temperature,
  spindle_speed,
  motor_current,
  torque,
  vibration,
  acoustic_level,
  bearing_temperature,
  process_temperature,
  hydraulic_pressure,
  flow_rate,
  coolant_temperature,
  material_feed_rate,
  feed_pressure,
  cycle_time,
  throughput_rate,
  production_volume,
  machine_utilization,
  resource_utilization,
  operator_load,
  power_consumption,
  energy_per_unit,
  defect_rate,
  quality_score,
  CAST(TRIM(first_pass_yield) AS DOUBLE),
  CAST(TRIM(fault_event_count) AS SIGNED),
  CAST(TRIM(downtime_minutes) AS DOUBLE),
  CAST(TRIM(maintenance_frequency) AS DOUBLE),
  CAST(TRIM(production_cost_per_unit) AS DECIMAL(12,4)),
  CAST(TRIM(resource_efficiency) AS DOUBLE),
  CAST(TRIM(production_efficiency) AS DOUBLE),
  CAST(TRIM(energy_saving_pct) AS DOUBLE),
  CAST(TRIM(downtime_reduction_pct) AS DOUBLE),
  CAST(TRIM(cost_reduction_pct) AS DOUBLE),
  CAST(TRIM(benefit_score) AS DOUBLE),
  TRIM(operation_mode)
FROM intelligent_production_iiot;
