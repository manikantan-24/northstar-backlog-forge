#!/usr/bin/env bash
# =============================================================================
# Backlog Synthesizer — CloudWatch alarm definitions
#
# Run ONCE after aws_setup.sh to create CloudWatch alarms and an SNS topic
# that notifies your on-call email.
#
# Prerequisites:
#   aws configure  (with sufficient permissions: cloudwatch:*, sns:*, logs:*)
#   The ECS cluster and services must already exist (created by aws_setup.sh).
#
# Usage:
#   chmod +x config/alerts/cloudwatch_alarms.sh
#   ./config/alerts/cloudwatch_alarms.sh
# =============================================================================

set -euo pipefail

# ── Configuration ─────────────────────────────────────────────────────────────
REGION="${AWS_REGION:-us-east-1}"
CLUSTER_NAME="backlog-synthesizer"
SERVICE_PROD="backlog-synthesizer"
SERVICE_STAGING="backlog-synthesizer-staging"
ALB_NAME="backlog-synthesizer"
ALERT_EMAIL="oncall@your-company.com"    # ← update

GREEN='\033[0;32m'; CYAN='\033[0;36m'; NC='\033[0m'
info() { echo -e "${CYAN}[INFO]${NC}  $*"; }
ok()   { echo -e "${GREEN}[OK]${NC}    $*"; }

# ── 1. SNS topic for alarm notifications ──────────────────────────────────────
info "Creating SNS topic: backlog-synthesizer-alerts"
TOPIC_ARN=$(aws sns create-topic \
  --name "backlog-synthesizer-alerts" \
  --region "$REGION" \
  --query TopicArn --output text)

aws sns subscribe \
  --topic-arn "$TOPIC_ARN" \
  --protocol email \
  --notification-endpoint "$ALERT_EMAIL" \
  --region "$REGION" \
  --output none 2>/dev/null || true

ok "SNS topic: $TOPIC_ARN"
info "Check $ALERT_EMAIL to confirm the subscription."

# ── Helper: create or update an alarm ─────────────────────────────────────────
alarm() {
  local name="$1"; shift
  aws cloudwatch put-metric-alarm \
    --alarm-name "$name" \
    --alarm-actions "$TOPIC_ARN" \
    --ok-actions "$TOPIC_ARN" \
    --region "$REGION" \
    "$@" \
    --output none
  echo "  alarm: $name"
}

# ── 2. ECS service CPU utilisation ───────────────────────────────────────────
info "Creating ECS CPU alarms"
alarm "backlog-prod-cpu-high" \
  --alarm-description "Prod ECS CPU > 80 % for 10 min" \
  --metric-name CPUUtilization \
  --namespace AWS/ECS \
  --dimensions "Name=ClusterName,Value=${CLUSTER_NAME}" "Name=ServiceName,Value=${SERVICE_PROD}" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 80 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching
ok "ECS CPU alarms ready"

# ── 3. ECS service memory utilisation ────────────────────────────────────────
info "Creating ECS memory alarms"
alarm "backlog-prod-memory-high" \
  --alarm-description "Prod ECS memory > 85 % for 10 min — risk of OOM kill" \
  --metric-name MemoryUtilization \
  --namespace AWS/ECS \
  --dimensions "Name=ClusterName,Value=${CLUSTER_NAME}" "Name=ServiceName,Value=${SERVICE_PROD}" \
  --statistic Average \
  --period 300 \
  --evaluation-periods 2 \
  --threshold 85 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching
ok "ECS memory alarms ready"

# ── 4. ALB 5xx error rate ─────────────────────────────────────────────────────
info "Creating ALB 5xx alarm"
ALB_ARN=$(aws elbv2 describe-load-balancers \
  --names "$ALB_NAME" \
  --query "LoadBalancers[0].LoadBalancerArn" \
  --output text --region "$REGION" 2>/dev/null || echo "")

if [ -n "$ALB_ARN" ] && [ "$ALB_ARN" != "None" ]; then
  # ALB dimension suffix is the part after "app/"
  ALB_SUFFIX=$(echo "$ALB_ARN" | sed 's|.*:loadbalancer/||')
  alarm "backlog-alb-5xx-high" \
    --alarm-description "ALB HTTP 5xx > 10 errors / min for 5 min" \
    --metric-name HTTPCode_ELB_5XX_Count \
    --namespace AWS/ApplicationELB \
    --dimensions "Name=LoadBalancer,Value=${ALB_SUFFIX}" \
    --statistic Sum \
    --period 60 \
    --evaluation-periods 5 \
    --threshold 10 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching
  ok "ALB 5xx alarm ready"
else
  info "  ALB not found — skipping 5xx alarm (run aws_setup.sh first)"
fi

# ── 5. ALB p99 target response time ──────────────────────────────────────────
info "Creating ALB latency alarm"
if [ -n "$ALB_ARN" ] && [ "$ALB_ARN" != "None" ]; then
  ALB_SUFFIX=$(echo "$ALB_ARN" | sed 's|.*:loadbalancer/||')
  alarm "backlog-alb-latency-high" \
    --alarm-description "ALB target response time p99 > 300s for 10 min" \
    --metric-name TargetResponseTime \
    --namespace AWS/ApplicationELB \
    --dimensions "Name=LoadBalancer,Value=${ALB_SUFFIX}" \
    --extended-statistic p99 \
    --period 300 \
    --evaluation-periods 2 \
    --threshold 300 \
    --comparison-operator GreaterThanThreshold \
    --treat-missing-data notBreaching
  ok "ALB latency alarm ready"
fi

# ── 6. Application error log metric filter + alarm ───────────────────────────
# Creates a CloudWatch metric filter on the ECS log group that counts lines
# containing "ERROR" or "CRITICAL" — works with both text and JSON log formats.
info "Creating application error log metric alarm"

LOG_GROUP="/ecs/${SERVICE_PROD}"
METRIC_NS="BacklogSynthesizer"
METRIC_NAME="ApplicationErrorCount"

aws logs put-metric-filter \
  --log-group-name "$LOG_GROUP" \
  --filter-name "ApplicationErrors" \
  --filter-pattern '"ERROR" || "CRITICAL" || "\"level\":\"error\"" || "\"level\":\"critical\""' \
  --metric-transformations \
    "metricName=${METRIC_NAME},metricNamespace=${METRIC_NS},metricValue=1,defaultValue=0" \
  --region "$REGION" \
  --output none 2>/dev/null || true

alarm "backlog-app-errors-high" \
  --alarm-description "Application error log lines > 5 in 5 min (likely synthesis failure spike)" \
  --metric-name "$METRIC_NAME" \
  --namespace "$METRIC_NS" \
  --statistic Sum \
  --period 300 \
  --evaluation-periods 1 \
  --threshold 5 \
  --comparison-operator GreaterThanThreshold \
  --treat-missing-data notBreaching
ok "Application error alarm ready"

# ── 7. CloudWatch Logs Insights query for cost reporting (saved query) ────────
info "Creating saved Logs Insights query for cost reporting"
aws logs put-query-definition \
  --name "BacklogSynthesizer/CostByUser" \
  --query-string \
    'fields @timestamp, @message
     | filter @message like "cost_usd"
     | parse @message "\"cost_usd\": *," as cost
     | parse @message "\"user_id\": \"*\"" as user_id
     | stats sum(cost) as total_cost, count() as runs by user_id
     | sort total_cost desc' \
  --log-group-names "/ecs/${SERVICE_PROD}" \
  --region "$REGION" \
  --output none 2>/dev/null || true
ok "Saved Logs Insights query: BacklogSynthesizer/CostByUser"

# ── 8. Eval regression — handled by CI ───────────────────────────────────────
info "NOTE: Eval regression alerting is handled by CI (ci.yml --fail-on-regression)."
info "      No separate CloudWatch rule needed for that case."

echo ""
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo -e "${GREEN}  CloudWatch alarms created successfully.      ${NC}"
echo -e "${GREEN}════════════════════════════════════════════════${NC}"
echo ""
echo "Alarms notify SNS → $ALERT_EMAIL"
echo "View alarms: https://console.aws.amazon.com/cloudwatch/home?region=${REGION}#alarmsV2:"
echo ""
