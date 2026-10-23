-- NEWREADME 7.4 规划的 MySQL 表结构（仅文档化，运行时未接入）
-- 当前版本按 FR-09 暂缓入库；jobs.jsonl 为唯一数据源。
-- 建议以 (job_title, company, captured_at) 建唯一约束，避免重复采集。

CREATE DATABASE IF NOT EXISTS recruiting
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_0900_ai_ci;

USE recruiting;

CREATE TABLE IF NOT EXISTS job_post (
  id           BIGINT       NOT NULL AUTO_INCREMENT COMMENT '主键',
  job_title    VARCHAR(255) NOT NULL              COMMENT '岗位名称',
  company      VARCHAR(255) NULL                  COMMENT '招聘单位',
  salary       VARCHAR(64)  NULL                  COMMENT '薪资区间',
  city         VARCHAR(64)  NULL                  COMMENT '城市',
  district     VARCHAR(64)  NULL                  COMMENT '区域',
  tags         JSON         NULL                  COMMENT '岗位标签',
  captured_at  DATETIME     NOT NULL              COMMENT '采集时间',
  created_at   DATETIME     NOT NULL DEFAULT CURRENT_TIMESTAMP,
  PRIMARY KEY (id),
  UNIQUE KEY uk_job_post (job_title, company, captured_at),
  KEY idx_job_post_city (city),
  KEY idx_job_post_captured (captured_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='岗位主表';

CREATE TABLE IF NOT EXISTS job_desc (
  id       BIGINT NOT NULL AUTO_INCREMENT COMMENT '主键',
  job_id   BIGINT NOT NULL                COMMENT '关联 job_post.id',
  seq      INT    NOT NULL                COMMENT '行序号（从 1 开始）',
  content  TEXT   NOT NULL                COMMENT '描述行内容',
  PRIMARY KEY (id),
  UNIQUE KEY uk_job_desc (job_id, seq),
  CONSTRAINT fk_job_desc_job FOREIGN KEY (job_id) REFERENCES job_post (id)
    ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COMMENT='职位描述行';

-- 与 jobs.jsonl 的映射建议：
-- job_title  -> job_post.job_title
-- company    -> job_post.company（当前原型可能为 NULL，见 AMBIGUITIES D14）
-- salary     -> job_post.salary
-- captured_at-> job_post.captured_at
-- job_desc[] -> job_desc(seq, content)
