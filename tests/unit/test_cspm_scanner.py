#!/usr/bin/env python3
"""
Unit tests for Cloud Security Posture Management (CSPM) & Kubernetes (KSPM) Scanner.
Tests Terraform IaC inspection, Kubernetes Pod Security Standards & RBAC,
cloud audit config ingestion, compliance scoring, and AI threat blast radius analysis.
"""

import json
from pathlib import Path
import pytest

from security.cspm_scanner import (
    CSPMScanner,
    CloudProvider,
    CSPMSeverity,
    CSPMResourceCategory,
    CSPMFinding,
    CSPMReport,
)
from ai_engine.advanced_engine import AdvancedAIEngine


@pytest.fixture
def scanner():
    return CSPMScanner()


def test_terraform_s3_and_security_group_detection(scanner):
    sample_tf = """
    resource "aws_s3_bucket" "insecure_data" {
      bucket = "corp-insecure-bucket"
      acl    = "public-read"
    }

    resource "aws_security_group" "open_ssh" {
      name = "allow-all-ssh"
      ingress {
        from_port   = 22
        to_port     = 22
        protocol    = "tcp"
        cidr_blocks = ["0.0.0.0/0"]
      }
    }

    resource "aws_iam_policy" "wildcard_admin" {
      name   = "admin-policy"
      policy = jsonencode({
        Action   = "*"
        Resource = "*"
      })
    }
    """
    findings = scanner.scan_terraform(sample_tf)
    assert len(findings) >= 3

    finding_ids = {f.finding_id for f in findings}
    assert "CSPM-AWS-S3-001" in finding_ids
    assert "CSPM-AWS-SG-22" in finding_ids
    assert "CSPM-AWS-IAM-001" in finding_ids

    s3_finding = next(f for f in findings if f.finding_id == "CSPM-AWS-S3-001")
    assert s3_finding.provider == CloudProvider.AWS
    assert s3_finding.severity == CSPMSeverity.CRITICAL
    assert "public-read" in s3_finding.evidence
    assert "aws s3api put-bucket-acl" in s3_finding.remediation_cli


def test_terraform_azure_and_gcp_detection(scanner):
    tf_azure_gcp = """
    resource "azurerm_storage_account" "unencrypted" {
      name                      = "unencryptedstorage"
      enable_https_traffic_only = false
    }

    resource "google_storage_bucket_iam_binding" "public" {
      bucket  = "public-gcs-bucket"
      members = ["allUsers"]
    }
    """
    findings = scanner.scan_terraform(tf_azure_gcp)
    finding_ids = {f.finding_id for f in findings}
    assert "CSPM-AZ-STORAGE-001" in finding_ids
    assert "CSPM-GCP-GCS-001" in finding_ids

    az_finding = next(f for f in findings if f.finding_id == "CSPM-AZ-STORAGE-001")
    assert az_finding.provider == CloudProvider.AZURE
    assert az_finding.severity == CSPMSeverity.HIGH


def test_kubernetes_manifest_pod_security_standards(scanner):
    k8s_yaml = """
apiVersion: apps/v1
kind: Deployment
metadata:
  name: vulnerable-deployment
  namespace: prod
spec:
  template:
    spec:
      hostNetwork: true
      hostPID: true
      volumes:
      - name: dockersock
        hostPath:
          path: /var/run/docker.sock
      containers:
      - name: app-container
        image: app:latest
        securityContext:
          privileged: true
          runAsNonRoot: false
          readOnlyRootFilesystem: false
        env:
        - name: API_SECRET_TOKEN
          value: "super_secret_token_12345"
    """
    findings = scanner.scan_kubernetes_manifest(k8s_yaml)
    assert len(findings) >= 5

    finding_ids = {f.finding_id for f in findings}
    assert "CSPM-K8S-PSS-001" in finding_ids  # hostNetwork
    assert "CSPM-K8S-PSS-002" in finding_ids  # hostPID
    assert "CSPM-K8S-PSS-003" in finding_ids  # sensitive hostPath
    assert "CSPM-K8S-PSS-004" in finding_ids  # privileged container
    assert "CSPM-K8S-PSS-005" in finding_ids  # runAsRoot
    assert "CSPM-K8S-SEC-001" in finding_ids  # plaintext secret

    priv_finding = next(f for f in findings if f.finding_id == "CSPM-K8S-PSS-004")
    assert priv_finding.severity == CSPMSeverity.CRITICAL
    assert priv_finding.provider == CloudProvider.KUBERNETES
    assert priv_finding.cvss_score >= 9.0


def test_kubernetes_rbac_and_service_audit(scanner):
    k8s_rbac = """
apiVersion: rbac.authorization.k8s.io/v1
kind: ClusterRoleBinding
metadata:
  name: anonymous-cluster-admin
roleRef:
  apiGroup: rbac.authorization.k8s.io
  kind: ClusterRole
  name: cluster-admin
subjects:
- kind: User
  name: system:anonymous
---
apiVersion: v1
kind: Service
metadata:
  name: public-nodeport-svc
  namespace: default
spec:
  type: NodePort
  ports:
  - port: 80
    targetPort: 8080
    """
    findings = scanner.scan_kubernetes_manifest(k8s_rbac)
    finding_ids = {f.finding_id for f in findings}
    assert "CSPM-K8S-RBAC-001" in finding_ids
    assert "CSPM-K8S-NET-001" in finding_ids

    rbac_finding = next(f for f in findings if f.finding_id == "CSPM-K8S-RBAC-001")
    assert rbac_finding.severity == CSPMSeverity.CRITICAL
    assert "kubectl delete" in rbac_finding.remediation_cli


def test_cloud_config_dump_ingestion(scanner):
    audit_data = {
        "s3_buckets": [
            {
                "name": "backup-data-store",
                "public_access": True,
                "encrypted": False,
            }
        ],
        "security_groups": [
            {
                "id": "sg-998877",
                "inbound_rules": [
                    {"from_port": 22, "to_port": 22, "cidr": "0.0.0.0/0"},
                    {"from_port": 3389, "to_port": 3389, "cidr": "0.0.0.0/0"},
                ],
            }
        ],
        "azure_key_vaults": [
            {
                "name": "kv-secrets-vault",
                "purge_protection_enabled": False,
            }
        ],
        "cloudtrail": {
            "logging": True,
            "is_multi_region_trail": False,
        },
    }
    findings = scanner.scan_cloud_config(audit_data)
    assert len(findings) >= 5

    finding_ids = {f.finding_id for f in findings}
    assert "CSPM-AWS-S3-001" in finding_ids
    assert "CSPM-AWS-SG-022" in finding_ids
    assert "CSPM-AWS-SG-3389" in finding_ids
    assert "CSPM-AZ-KV-001" in finding_ids
    assert "CSPM-AWS-TRAIL-001" in finding_ids


def test_mock_benchmark_and_scoring(scanner):
    report = scanner.scan_mock()
    assert isinstance(report, CSPMReport)
    assert report.total_findings > 0
    assert report.critical_count > 0
    assert report.high_count > 0
    assert 0.0 <= report.compliance_score <= 100.0
    assert 0.0 <= report.cis_pass_rate <= 100.0

    # Provider breakdown should span multi-cloud
    assert CloudProvider.AWS.value in report.provider_summary or CloudProvider.AWS.value in report.provider_breakdown
    assert CloudProvider.KUBERNETES.value in report.provider_summary or CloudProvider.KUBERNETES.value in report.provider_breakdown

    # Test remediation export
    bash_script = report.export_remediation_script(script_format="bash")
    assert "#!/bin/bash" in bash_script
    assert "aws " in bash_script or "kubectl " in bash_script

    tf_patch = report.export_remediation_script(script_format="terraform")
    assert "Terraform" in tf_patch


def test_scan_path_temporary_files(scanner, tmp_path):
    k8s_file = tmp_path / "workload.yaml"
    k8s_file.write_text("""
apiVersion: v1
kind: Pod
metadata:
  name: privileged-pod
spec:
  containers:
  - name: test-c
    image: alpine
    securityContext:
      privileged: true
    """, encoding="utf-8")

    tf_file = tmp_path / "infra.tf"
    tf_file.write_text("""
resource "aws_s3_bucket" "test_bucket" {
  bucket = "test-bucket-name"
  acl    = "public-read"
}
    """, encoding="utf-8")

    report = scanner.scan_path(str(tmp_path))
    assert report.total_findings >= 2
    f_ids = {f.finding_id for f in report.findings}
    assert "CSPM-K8S-PSS-004" in f_ids
    assert "CSPM-AWS-S3-001" in f_ids


def test_ai_engine_cloud_posture_integration(scanner):
    ai_engine = AdvancedAIEngine()
    report = scanner.scan_mock()

    threat_model = ai_engine.analyze_cloud_posture(report)
    assert isinstance(threat_model, dict)
    assert "assessed_blast_radius" in threat_model
    assert "synthesized_attack_scenarios" in threat_model
    assert len(threat_model["synthesized_attack_scenarios"]) > 0
    assert "ai_hardening_priority" in threat_model
    assert threat_model["critical_risk_count"] == report.critical_count
