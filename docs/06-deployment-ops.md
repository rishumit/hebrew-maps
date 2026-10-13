# תפעול, פריסה, וניטור

## 1. סביבות (Environments)

```
Development → Staging → Production
    ↓           ↓           ↓
  Local      Test Env    Live Users
```

### 1.1 Development
**Purpose:** פיתוח מקומי

**Infrastructure:**
- Docker Compose
- Local PostgreSQL + PostGIS
- Local Elasticsearch
- Mock external APIs

**URL:** `http://localhost:3000`

**Data:** Sample dataset (~1000 POI)

---

### 1.2 Staging
**Purpose:** בדיקות pre-production

**Infrastructure:**
- Cloud environment (mirror of production)
- Full dataset
- Real external API integrations

**URL:** `https://staging.maps.example.co.il`

**Access:** Development team only

---

### 1.3 Production
**Purpose:** Live users

**Infrastructure:**
- Load balanced
- Auto-scaling
- CDN
- Full monitoring

**URL:** `https://maps.example.co.il`

**SLA:** 99.5% uptime

---

## 2. Deployment Strategy

### 2.1 CI/CD Pipeline

```
Code Push → GitHub
    ↓
GitHub Actions / GitLab CI
    ├─ Lint & Format check
    ├─ Unit tests
    ├─ Integration tests
    ├─ Build Docker images
    ├─ Security scan
    ├─ E2E tests (staging)
    └─ Deploy to staging
         ↓
    Manual approval
         ↓
    Deploy to production
    (Blue-Green or Canary)
```

---

### 2.2 Blue-Green Deployment

**Strategy:** אפס downtime

```
┌───────────────┐
│ Load Balancer │
└───────┬───────┘
        │
   ┌────┴────┐
   │         │
┌──▼──┐   ┌──▼──┐
│BLUE │   │GREEN│
│(old)│   │(new)│
└─────┘   └─────┘

Step 1: Deploy GREEN (new version)
Step 2: Test GREEN
Step 3: Switch traffic → GREEN
Step 4: Keep BLUE for rollback
```

**Rollback:** מעבר חזרה ל-BLUE תוך דקות

---

### 2.3 Deployment Checklist

**Pre-Deployment:**
- [ ] All tests pass
- [ ] Security scan clean
- [ ] Database migrations tested
- [ ] Rollback plan ready
- [ ] Monitoring alerts configured
- [ ] Team notified

**Deployment:**
- [ ] Deploy to staging
- [ ] Smoke tests on staging
- [ ] Deploy to production (blue-green)
- [ ] Health checks pass
- [ ] Switch traffic
- [ ] Monitor error rates

**Post-Deployment:**
- [ ] Verify key metrics
- [ ] Check error logs
- [ ] User acceptance spot check
- [ ] Update status page
- [ ] Team debrief

---

## 3. Infrastructure Architecture

### 3.1 Production Setup (Cloud)

```
        Internet
           ↓
    ┌──────────────┐
    │   CDN        │ (Cloudflare / CloudFront)
    │  (Static)    │
    └──────┬───────┘
           ↓
    ┌──────────────┐
    │Load Balancer │
    └──────┬───────┘
           │
    ┌──────┴────────────────┐
    │                       │
┌───▼────┐          ┌───────▼──┐
│Frontend│          │  Backend │
│Servers │          │  (API)   │
│(Nginx) │          │  Cluster │
└────────┘          └─────┬────┘
                          │
           ┌──────────────┼──────────────┐
           │              │              │
    ┌──────▼─────┐ ┌─────▼──────┐ ┌────▼────┐
    │PostgreSQL  │ │Elasticsearch│ │ Object  │
    │+ PostGIS   │ │  (Search)   │ │ Storage │
    │(Primary+   │ └─────────────┘ │ (S3)    │
    │ Replicas)  │                 └─────────┘
    └────────────┘
```

---

### 3.2 Scaling Strategy

#### Horizontal Scaling
**API Servers:**
- Auto-scale based on CPU > 70%
- Min: 2 instances
- Max: 10 instances

**Database:**
- 1 Primary (writes)
- 2+ Read Replicas (reads)
- Connection pooling

#### Vertical Scaling
**When needed:**
- Database storage growth
- Search index size increase

---

### 3.3 Backup Strategy

**Database Backups:**
- Full backup: Daily at 2 AM
- Incremental: Every 6 hours
- Retention: 30 days
- Off-site replication: Yes

**Object Storage:**
- Versioning enabled
- Lifecycle policies (archive after 90 days)

**Test Restores:**
- Monthly restore drill
- Document restore time (RTO: 2 hours)

---

## 4. Monitoring & Alerting

### 4.1 Key Metrics

#### Application Metrics
| Metric | Target | Alert Threshold |
|--------|--------|-----------------|
| API Response Time (p95) | < 500ms | > 1000ms |
| Error Rate | < 0.1% | > 1% |
| Uptime | 99.5% | < 99% |
| Requests/sec | - | Baseline + 200% |

#### Infrastructure Metrics
| Metric | Target | Alert |
|--------|--------|-------|
| CPU Usage | < 70% | > 80% |
| Memory Usage | < 80% | > 90% |
| Disk Usage | < 70% | > 85% |
| Database Connections | < 80% max | > 90% |

---

### 4.2 Monitoring Stack

**Components:**
- **Prometheus** - Metrics collection
- **Grafana** - Dashboards
- **Loki** - Log aggregation
- **Alertmanager** - Alert routing

**Dashboards:**
1. **System Health** - CPU, RAM, Disk, Network
2. **Application Performance** - Latency, throughput, errors
3. **User Experience** - Page load times, search performance
4. **Database** - Query times, connections, replication lag

---

### 4.3 Alerting Rules

**Critical Alerts** (PagerDuty / SMS)
```yaml
- name: API Down
  condition: up{job="api"} == 0
  duration: 2m
  severity: critical
  
- name: High Error Rate
  condition: rate(http_errors_total[5m]) > 0.01
  duration: 5m
  severity: critical
  
- name: Database Down
  condition: up{job="postgres"} == 0
  duration: 1m
  severity: critical
```

**Warning Alerts** (Slack)
```yaml
- name: High Latency
  condition: http_request_duration_p95 > 1s
  duration: 10m
  severity: warning
  
- name: Disk Space Low
  condition: disk_usage_percent > 85
  duration: 15m
  severity: warning
```

---

### 4.4 Log Management

**Structure:**
```json
{
  "timestamp": "2026-10-12T14:30:00Z",
  "level": "error",
  "service": "api",
  "trace_id": "abc123",
  "user_id": "user-456",
  "message": "Failed to fetch POI",
  "error": "Connection timeout",
  "duration_ms": 5000
}
```

**Log Levels:**
- `DEBUG` - Development only
- `INFO` - Normal operations
- `WARN` - Recoverable errors
- `ERROR` - Failures requiring attention
- `CRITICAL` - System down

**Retention:**
- Hot storage: 7 days
- Warm storage: 30 days
- Archive (S3): 1 year

---

## 5. Security Operations

### 5.1 Security Monitoring

**Tools:**
- WAF (Web Application Firewall)
- DDoS protection
- SSL/TLS certificates (auto-renew)
- Intrusion detection

**Security Logs:**
- Failed login attempts
- Rate limit violations
- Suspicious queries (SQL injection attempts)
- Unusual traffic patterns

---

### 5.2 Incident Response Plan

**P0 (Critical - System Down)**
```
1. Alert fires → On-call engineer paged
2. Acknowledge within 5 minutes
3. Triage: Check monitoring dashboards
4. Communication: Update status page
5. Mitigation: Rollback or hotfix
6. Resolution: Verify system stable
7. Post-mortem: Document within 48h
```

**P1 (High - Major Feature Down)**
- Response time: 30 minutes
- Fix time: 4 hours

**P2 (Medium - Minor Issue)**
- Response time: 2 hours
- Fix time: 1 business day

---

### 5.3 Security Checklist

**Weekly:**
- [ ] Review security logs
- [ ] Check for failed auth attempts
- [ ] Verify SSL certificates valid

**Monthly:**
- [ ] Dependency vulnerability scan (`npm audit`)
- [ ] Review access logs
- [ ] Update security patches

**Quarterly:**
- [ ] Penetration testing
- [ ] Security audit
- [ ] Disaster recovery drill

---

## 6. Performance Optimization

### 6.1 Caching Strategy

**Browser Cache:**
```
Tiles: Cache-Control: public, max-age=86400 (24h)
Static assets: Cache-Control: public, max-age=31536000 (1y)
API responses: Cache-Control: private, max-age=300 (5m)
```

**CDN Cache:**
- Tiles: 7 days
- Images: 30 days
- API: No cache (dynamic)

**Application Cache (Redis):**
```
Search results: TTL 5 minutes
POI data: TTL 1 hour
Categories: TTL 24 hours
```

---

### 6.2 Database Optimization

**Indexes:**
```sql
-- Spatial index
CREATE INDEX idx_poi_location ON poi USING GIST(location);

-- Search index
CREATE INDEX idx_poi_name_he_trgm ON poi USING GIN(name_he gin_trgm_ops);

-- Category index
CREATE INDEX idx_poi_category ON poi(category_id) WHERE deleted_at IS NULL;
```

**Query Optimization:**
- Use EXPLAIN ANALYZE
- Avoid N+1 queries
- Connection pooling (max 50 connections)

**Materialized Views:**
```sql
-- Pre-compute popular searches
CREATE MATERIALIZED VIEW popular_pois AS
SELECT * FROM poi
WHERE rating >= 4.5 AND review_count >= 100
ORDER BY rating DESC, review_count DESC;

REFRESH MATERIALIZED VIEW popular_pois; -- Daily cron
```

---

## 7. Cost Optimization

### 7.1 Cloud Resources

**Compute:**
- Use spot instances for non-critical workers
- Right-size instances (monitor CPU/RAM usage)
- Auto-scaling during off-peak hours

**Storage:**
- Object storage lifecycle policies
- Compress tiles (gzip)
- Archive old backups to glacier

**Bandwidth:**
- CDN for static assets (reduce origin bandwidth)
- Optimize tile size (vector tiles < raster)

---

### 7.2 Budget Alerts

**Monthly Budget:** $X

**Alerts:**
- 50% spent → Slack notification
- 80% spent → Email to team lead
- 90% spent → Freeze non-essential resources
- 100% → Emergency review

---

## 8. Disaster Recovery

### 8.1 Recovery Objectives

**RTO (Recovery Time Objective):** 2 hours  
**RPO (Recovery Point Objective):** 6 hours (max data loss)

---

### 8.2 Disaster Scenarios

**Database Failure:**
```
1. Detect failure (monitoring alerts)
2. Promote read replica to primary
3. Update DNS / connection strings
4. Verify application connectivity
5. Restore from backup if needed
Time: 30 minutes
```

**Complete Region Outage:**
```
1. Failover to secondary region
2. Update DNS to secondary load balancer
3. Verify all services operational
4. Monitor performance
Time: 1-2 hours
```

**Data Corruption:**
```
1. Identify corruption time
2. Stop writes to database
3. Restore from last good backup
4. Replay transaction logs (if available)
5. Verify data integrity
Time: 2-4 hours
```

---

### 8.3 DR Drills

**Quarterly:**
- Simulate database failure
- Test failover process
- Document actual recovery time
- Update runbooks

---

## 9. Operational Runbooks

### 9.1 Common Tasks

#### Deploy New Version
```bash
# 1. Checkout release branch
git checkout release/v1.2.0

# 2. Build and push Docker images
docker build -t maps-api:v1.2.0 .
docker push maps-api:v1.2.0

# 3. Deploy to staging
kubectl apply -f k8s/staging/

# 4. Run smoke tests
npm run test:e2e:staging

# 5. Deploy to production (blue-green)
kubectl apply -f k8s/production/
kubectl set image deployment/api api=maps-api:v1.2.0

# 6. Monitor
kubectl rollout status deployment/api
watch kubectl get pods
```

#### Scale Backend
```bash
# Manual scale
kubectl scale deployment/api --replicas=5

# Auto-scale
kubectl autoscale deployment/api --min=2 --max=10 --cpu-percent=70
```

#### Database Maintenance
```bash
# Vacuum (weekly)
psql -c "VACUUM ANALYZE poi;"

# Reindex (monthly)
psql -c "REINDEX TABLE poi;"

# Update statistics
psql -c "ANALYZE poi;"
```

---

## 10. On-Call Rotation

**Schedule:**
- Primary: Week rotation
- Secondary: Backup
- Escalation: Team lead

**Responsibilities:**
- Monitor alerts
- Respond to incidents
- Document issues
- Handoff notes

**Compensation:**
- On-call pay
- Time-off after major incident

---

*מסמך זה מספק את כל המידע הנדרש לתפעול בטוח ויציב של המערכת.*
