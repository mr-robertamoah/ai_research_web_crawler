"""lib/keywords/news_monitoring.py — keyword taxonomy for news/tech blog monitoring."""

KEYWORD_GROUPS = {
    "platform_engineering": [
        "platform engineering", "internal developer platform", "idp",
        "developer experience", "devex", "golden path",
    ],
    "devops": [
        "devops", "ci/cd", "continuous integration", "continuous delivery",
        "continuous deployment", "gitops", "pipeline automation",
    ],
    "data_engineering": [
        "data engineering", "data pipeline", "data lakehouse",
        "apache spark", "apache flink", "dbt", "data mesh",
    ],
    "mlops": [
        "mlops", "ml pipeline", "model registry", "model serving",
        "feature store", "ml monitoring", "drift detection",
    ],
    "aiops": [
        "aiops", "ai operations", "intelligent operations",
        "observability ai", "anomaly detection",
    ],
    "ai_engineering": [
        "ai engineering", "genai", "generative ai", "llm", "large language model",
        "rag", "retrieval augmented", "ai agent", "agentic", "prompt engineering",
        "fine tuning", "foundation model", "multimodal",
    ],
    "devsecops": [
        "devsecops", "shift left security", "supply chain security",
        "sbom", "software bill of materials", "sast", "dast",
    ],
    "security": [
        "security", "zero trust", "vulnerability", "cve", "exploit",
        "ransomware", "breach", "patch", "critical update",
    ],
    "tools": [
        "kubernetes", "k8s", "docker", "helm", "argocd", "flux",
        "terraform", "opentofu", "ansible", "jenkins", "github actions",
        "gitlab", "keycloak", "rhbk", "linux", "prometheus", "grafana",
        "opentelemetry", "istio", "envoy",
    ],
    "cloud": [
        "aws", "azure", "gcp", "google cloud", "multi cloud", "hybrid cloud",
        "digitalocean", "hetzner", "cloudflare", "serverless",
    ],
    "concepts": [
        "infrastructure as code", "iac", "automation", "microservices",
        "service mesh", "event driven", "api gateway", "chaos engineering",
        "site reliability", "sre", "finops", "green software",
    ],
}

# URLs that match these keywords trigger alerts regardless of priority score
ALERT_KEYWORDS = [
    "critical", "cve", "vulnerability", "exploit", "breach", "ransomware",
    "zero day", "0day", "patch", "emergency", "incident",
    "major release", "breaking change", "deprecat",
]
