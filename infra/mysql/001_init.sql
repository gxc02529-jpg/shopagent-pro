CREATE DATABASE IF NOT EXISTS shopagent CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
USE shopagent;

CREATE TABLE IF NOT EXISTS shopagent_interactions (
  trace_id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  intent VARCHAR(64) NOT NULL,
  routed_agent VARCHAR(128) NULL,
  need_human BOOLEAN NOT NULL,
  latency_ms DOUBLE NOT NULL,
  created_at DATETIME(6) NOT NULL,
  payload LONGTEXT NOT NULL,
  INDEX idx_interactions_user (user_id),
  INDEX idx_interactions_intent (intent),
  INDEX idx_interactions_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopagent_feedback (
  id VARCHAR(32) PRIMARY KEY,
  trace_id VARCHAR(64) NOT NULL,
  user_id VARCHAR(128) NOT NULL,
  rating INT NOT NULL,
  idempotency_key VARCHAR(128) NULL,
  created_at DATETIME(6) NOT NULL,
  payload LONGTEXT NOT NULL,
  INDEX idx_feedback_trace (trace_id),
  INDEX idx_feedback_user (user_id),
  UNIQUE KEY uq_feedback_user_idempotency (user_id, idempotency_key),
  CONSTRAINT chk_feedback_rating CHECK (rating BETWEEN 1 AND 5)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopagent_audit_events (
  id VARCHAR(32) PRIMARY KEY,
  event_type VARCHAR(64) NOT NULL,
  actor_id VARCHAR(128) NOT NULL,
  entity_id VARCHAR(64) NOT NULL,
  trace_id VARCHAR(64) NULL,
  created_at DATETIME(6) NOT NULL,
  payload LONGTEXT NOT NULL,
  INDEX idx_audit_type (event_type),
  INDEX idx_audit_actor (actor_id),
  INDEX idx_audit_entity (entity_id),
  INDEX idx_audit_trace (trace_id),
  INDEX idx_audit_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopagent_knowledge_candidates (
  id VARCHAR(32) PRIMARY KEY,
  status VARCHAR(32) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  payload LONGTEXT NOT NULL,
  INDEX idx_candidates_status (status)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopagent_knowledge_documents (
  id VARCHAR(64) PRIMARY KEY,
  domain VARCHAR(64) NOT NULL,
  version VARCHAR(64) NOT NULL,
  updated_at DATETIME(6) NOT NULL,
  payload LONGTEXT NOT NULL,
  INDEX idx_knowledge_domain (domain)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopagent_orders (
  id VARCHAR(64) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  payload LONGTEXT NOT NULL,
  INDEX idx_orders_user (user_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopagent_after_sales_tickets (
  id VARCHAR(32) PRIMARY KEY,
  user_id VARCHAR(128) NOT NULL,
  order_id VARCHAR(64) NOT NULL,
  idempotency_key VARCHAR(128) NULL,
  created_at DATETIME(6) NOT NULL,
  payload LONGTEXT NOT NULL,
  UNIQUE KEY uq_ticket_user_idempotency (user_id, idempotency_key),
  INDEX idx_tickets_user (user_id),
  INDEX idx_tickets_order (order_id),
  INDEX idx_tickets_created (created_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS shopagent_a2a_tasks (
  id VARCHAR(64) PRIMARY KEY,
  owner VARCHAR(128) NOT NULL,
  status VARCHAR(64) NOT NULL,
  created_at DATETIME(6) NOT NULL,
  expires_at DATETIME(6) NOT NULL,
  payload LONGTEXT NOT NULL,
  INDEX idx_a2a_tasks_owner (owner),
  INDEX idx_a2a_tasks_status (status),
  INDEX idx_a2a_tasks_created (created_at),
  INDEX idx_a2a_tasks_expires (expires_at)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
