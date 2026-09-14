-- 01_drop_legacy_dim_tables.sql
--
-- 路线 B（单表宽表模型）：删除历史派生维表。
--
-- 为什么可以删：
--   这 4 张表原先由 intelligent_production_iiot 通过 SELECT DISTINCT 派生，
--   属性列（dim_line.description / dim_product.product_name）从未填充，
--   而 fact_production_record 已冗余保存全部维度编码。
--   实测 fact JOIN 任意 dim 的结果与直接读 fact 完全相同（15000 行不丢不多），
--   即 JOIN 不带来任何信息，只会让 LLM 生成无收益的关联查询、增加出错面。
--   数据本身没有丢失：原始层 intelligent_production_iiot 保持不变，
--   如需回退到星型模型（路线 A），可从原始层重新派生。
--
-- 详见 docs/DATA_MODEL.md

DROP TABLE IF EXISTS dim_line;
DROP TABLE IF EXISTS dim_machine;
DROP TABLE IF EXISTS dim_product;
DROP TABLE IF EXISTS dim_batch;
