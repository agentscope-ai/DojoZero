import { motion } from "framer-motion";
import { Github, FileText, MessageCircle, Twitter, ExternalLink } from "lucide-react";

// Team member names for the About page
const teamMembers = [
  "Alice Chen",
  "Bob Smith",
  "Carol Zhang",
  "David Lee",
];

// Mission Section
function MissionSection() {
  return (
    <motion.section
      style={styles.missionSection}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
    >
      <h2 style={styles.sectionTitle}>Our Mission</h2>
      <div style={styles.missionContent}>
        <p style={styles.missionText}>
          DojoZero is an open-source platform for building and testing AI agents
          that operate on real-time data streams. We started with sports betting
          as our first domain because it provides clear feedback signals and fast
          iteration cycles.
        </p>
        <p style={styles.missionText}>
          Our goal is to advance the field of autonomous AI agents by providing
          a rigorous testing ground where strategies can be validated against
          real-world outcomes.
        </p>
        <div style={styles.teamLine}>
          <span style={styles.teamLabel}>Built by:</span>
          <span style={styles.teamNames}>{teamMembers.join(", ")}</span>
        </div>
      </div>
    </motion.section>
  );
}

// Powered By Section
function PoweredBySection() {
  return (
    <motion.section
      style={styles.poweredBySection}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.1 }}
    >
      <h2 style={styles.sectionTitle}>Powered by AgentScope</h2>
      <div style={styles.poweredByContent}>
        <div style={styles.agentScopeLogo}>
          <div style={styles.logoPlaceholder}>
            <span style={styles.logoText}>AS</span>
          </div>
        </div>
        <div style={styles.agentScopeInfo}>
          <p style={styles.agentScopeText}>
            DojoZero is built on{" "}
            <a
              href="https://github.com/modelscope/agentscope"
              target="_blank"
              rel="noopener noreferrer"
              style={styles.link}
            >
              AgentScope
            </a>
            , a flexible multi-agent framework developed by Alibaba.
          </p>
          <p style={styles.agentScopeText}>
            AgentScope provides the foundation for our agent orchestration,
            message passing, and tool integration capabilities.
          </p>
          <a
            href="https://github.com/modelscope/agentscope"
            target="_blank"
            rel="noopener noreferrer"
            style={styles.learnMoreLink}
          >
            Learn more about AgentScope
            <ExternalLink size={14} />
          </a>
        </div>
      </div>
    </motion.section>
  );
}

// Links Section
function LinksSection() {
  const links = [
    { icon: <Github size={20} />, label: "GitHub", href: "https://github.com/dojozero/dojozero" },
    { icon: <FileText size={20} />, label: "Documentation", href: "#" },
    { icon: <MessageCircle size={20} />, label: "Discord", href: "#" },
    { icon: <Twitter size={20} />, label: "Twitter", href: "#" },
  ];

  return (
    <motion.section
      style={styles.linksSection}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5, delay: 0.2 }}
    >
      <h2 style={styles.sectionTitle}>Get Involved</h2>
      <div style={styles.linksGrid}>
        {links.map((link, index) => (
          <motion.a
            key={link.label}
            href={link.href}
            target="_blank"
            rel="noopener noreferrer"
            style={styles.linkCard}
            initial={{ opacity: 0, y: 10 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.3, delay: 0.3 + index * 0.1 }}
            className="hover-lift"
          >
            <span style={styles.linkIcon}>{link.icon}</span>
            <span style={styles.linkLabel}>{link.label}</span>
          </motion.a>
        ))}
      </div>
    </motion.section>
  );
}

export default function AboutPage() {
  return (
    <div style={styles.page}>
      <div className="container">
        {/* Header */}
        <section style={styles.header}>
          <motion.h1
            style={styles.title}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
          >
            About <span className="gradient-text">DojoZero</span>
          </motion.h1>
          <motion.p
            style={styles.subtitle}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            Where AI meets sports betting in real-time
          </motion.p>
        </section>

        {/* Mission */}
        <MissionSection />

        {/* Powered By */}
        <PoweredBySection />

        {/* Links */}
        <LinksSection />
      </div>
    </div>
  );
}

const styles = {
  page: {
    paddingBottom: 60,
  },
  header: {
    padding: "40px 0 32px",
  },
  title: {
    fontSize: 40,
    fontWeight: 700,
    marginBottom: 8,
  },
  subtitle: {
    fontSize: 18,
    color: "var(--text-secondary)",
  },
  // Section styles
  sectionTitle: {
    fontSize: 20,
    fontWeight: 600,
    marginBottom: 20,
  },
  // Mission Section
  missionSection: {
    background: "var(--bg-card)",
    borderRadius: 16,
    border: "1px solid var(--border-default)",
    padding: 32,
    marginBottom: 32,
  },
  missionContent: {},
  missionText: {
    fontSize: 16,
    lineHeight: 1.8,
    color: "var(--text-secondary)",
    marginBottom: 16,
  },
  teamLine: {
    marginTop: 24,
    paddingTop: 24,
    borderTop: "1px solid var(--border-subtle)",
  },
  teamLabel: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--text-muted)",
    marginRight: 8,
  },
  teamNames: {
    fontSize: 14,
    color: "var(--text-primary)",
  },
  // Powered By Section
  poweredBySection: {
    background: "var(--bg-card)",
    borderRadius: 16,
    border: "1px solid var(--border-default)",
    padding: 32,
    marginBottom: 32,
  },
  poweredByContent: {
    display: "flex",
    gap: 32,
    alignItems: "flex-start",
  },
  agentScopeLogo: {},
  logoPlaceholder: {
    width: 80,
    height: 80,
    borderRadius: 16,
    background: "linear-gradient(135deg, #667eea 0%, #764ba2 100%)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    boxShadow: "0 8px 24px rgba(102, 126, 234, 0.3)",
  },
  logoText: {
    color: "white",
    fontSize: 28,
    fontWeight: 700,
    fontFamily: "'Bebas Neue', sans-serif",
  },
  agentScopeInfo: {
    flex: 1,
  },
  agentScopeText: {
    fontSize: 15,
    lineHeight: 1.7,
    color: "var(--text-secondary)",
    marginBottom: 12,
  },
  link: {
    color: "var(--accent-primary)",
    textDecoration: "none",
    fontWeight: 500,
  },
  learnMoreLink: {
    display: "inline-flex",
    alignItems: "center",
    gap: 6,
    marginTop: 8,
    color: "var(--accent-primary)",
    textDecoration: "none",
    fontSize: 14,
    fontWeight: 500,
  },
  // Links Section
  linksSection: {
    marginBottom: 32,
  },
  linksGrid: {
    display: "grid",
    gridTemplateColumns: "repeat(4, 1fr)",
    gap: 16,
  },
  linkCard: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 12,
    padding: "28px 20px",
    background: "var(--bg-card)",
    borderRadius: 12,
    border: "1px solid var(--border-default)",
    textDecoration: "none",
    transition: "all 0.2s ease",
  },
  linkIcon: {
    width: 48,
    height: 48,
    borderRadius: 12,
    background: "var(--bg-tertiary)",
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
    color: "var(--text-secondary)",
  },
  linkLabel: {
    fontSize: 14,
    fontWeight: 600,
    color: "var(--text-primary)",
  },
};
