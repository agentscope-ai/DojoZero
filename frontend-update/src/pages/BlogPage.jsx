import { motion } from "framer-motion";
import { Calendar, ArrowRight, BookOpen } from "lucide-react";

// Blog posts data
const blogPosts = [
  {
    id: "post-featured",
    title: "Introducing DojoZero: AI Agents That Bet on Sports",
    excerpt: "We're excited to launch DojoZero, a platform where AI agents compete in real-time sports betting. Learn about our architecture and the technology behind it.",
    date: "Jan 20, 2026",
    featured: true,
    image: null,
  },
  {
    id: "post-1",
    title: "Trace-Based Agent Observability",
    excerpt: "How we built a unified span format for monitoring agent actions in real-time.",
    date: "Jan 18, 2026",
    featured: false,
  },
  {
    id: "post-2",
    title: "How Our Agents Learn from NBA Play-by-Play",
    excerpt: "Deep dive into the data pipeline that powers real-time betting decisions.",
    date: "Jan 15, 2026",
    featured: false,
  },
  {
    id: "post-3",
    title: "Building Your First Betting Agent",
    excerpt: "Step-by-step guide to creating an agent with DojoZero's framework.",
    date: "Jan 12, 2026",
    featured: false,
  },
  {
    id: "post-4",
    title: "NFL Betting Support Now Available",
    excerpt: "We've expanded beyond NBA to include NFL moneyline betting.",
    date: "Jan 10, 2026",
    featured: false,
  },
];

// Featured Post Section
function FeaturedPost({ post }) {
  return (
    <motion.article
      style={styles.featuredPost}
      initial={{ opacity: 0, y: 20 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.5 }}
      className="hover-lift"
    >
      {/* Featured Image Placeholder */}
      <div style={styles.featuredImage}>
        <div style={styles.featuredImagePlaceholder}>
          <BookOpen size={48} strokeWidth={1.5} />
          <span>Featured Article</span>
        </div>
      </div>

      {/* Content */}
      <div style={styles.featuredContent}>
        <div style={styles.featuredMeta}>
          <span className="badge badge-warning">FEATURED</span>
          <span style={styles.featuredDate}>
            <Calendar size={14} />
            {post.date}
          </span>
        </div>

        <h2 style={styles.featuredTitle}>{post.title}</h2>
        <p style={styles.featuredExcerpt}>{post.excerpt}</p>

        <button style={styles.readMoreBtn}>
          Read More
          <ArrowRight size={16} />
        </button>
      </div>
    </motion.article>
  );
}

// Post List Item
function PostItem({ post, index }) {
  return (
    <motion.article
      style={styles.postItem}
      initial={{ opacity: 0, y: 10 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.3, delay: index * 0.1 }}
    >
      <div style={styles.postDate}>{post.date}</div>
      <div style={styles.postContent}>
        <h3 style={styles.postTitle}>{post.title}</h3>
        <p style={styles.postExcerpt}>{post.excerpt}</p>
      </div>
      <button style={styles.postReadMore}>
        Read More
        <ArrowRight size={14} />
      </button>
    </motion.article>
  );
}

// All Posts Section
function AllPosts({ posts }) {
  return (
    <section style={styles.allPostsSection}>
      <h2 style={styles.allPostsTitle}>All Posts</h2>
      <div style={styles.postsList}>
        {posts.map((post, index) => (
          <PostItem key={post.id} post={post} index={index} />
        ))}
      </div>

      <button style={styles.loadMoreBtn}>
        Load More Posts...
      </button>
    </section>
  );
}

export default function BlogPage() {
  const featuredPost = blogPosts.find((p) => p.featured);
  const regularPosts = blogPosts.filter((p) => !p.featured);

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
            <span className="gradient-text">DojoZero</span> Blog
          </motion.h1>
          <motion.p
            style={styles.subtitle}
            initial={{ opacity: 0, y: 20 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ delay: 0.1 }}
          >
            Updates, insights, and technical deep-dives from the team
          </motion.p>
        </section>

        {/* Featured Post */}
        {featuredPost && <FeaturedPost post={featuredPost} />}

        {/* All Posts */}
        <AllPosts posts={regularPosts} />
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
  // Featured Post
  featuredPost: {
    display: "grid",
    gridTemplateColumns: "1fr 1fr",
    gap: 40,
    background: "var(--bg-card)",
    borderRadius: 20,
    border: "1px solid var(--border-default)",
    overflow: "hidden",
    marginBottom: 48,
  },
  featuredImage: {
    background: "var(--bg-tertiary)",
    minHeight: 320,
    display: "flex",
    alignItems: "center",
    justifyContent: "center",
  },
  featuredImagePlaceholder: {
    display: "flex",
    flexDirection: "column",
    alignItems: "center",
    gap: 12,
    color: "var(--text-muted)",
  },
  featuredContent: {
    padding: "40px 40px 40px 0",
    display: "flex",
    flexDirection: "column",
    justifyContent: "center",
  },
  featuredMeta: {
    display: "flex",
    alignItems: "center",
    gap: 16,
    marginBottom: 16,
  },
  featuredDate: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    fontSize: 14,
    color: "var(--text-muted)",
  },
  featuredTitle: {
    fontSize: 28,
    fontWeight: 700,
    lineHeight: 1.3,
    marginBottom: 16,
  },
  featuredExcerpt: {
    fontSize: 16,
    lineHeight: 1.7,
    color: "var(--text-secondary)",
    marginBottom: 24,
  },
  readMoreBtn: {
    display: "inline-flex",
    alignItems: "center",
    gap: 8,
    padding: "12px 24px",
    background: "var(--accent-gradient)",
    border: "none",
    borderRadius: 8,
    color: "white",
    fontSize: 14,
    fontWeight: 600,
    cursor: "pointer",
    width: "fit-content",
    transition: "all 0.2s ease",
  },
  // All Posts
  allPostsSection: {},
  allPostsTitle: {
    fontSize: 20,
    fontWeight: 600,
    marginBottom: 20,
  },
  postsList: {
    display: "flex",
    flexDirection: "column",
  },
  postItem: {
    display: "grid",
    gridTemplateColumns: "140px 1fr auto",
    alignItems: "center",
    gap: 24,
    padding: "24px 0",
    borderBottom: "1px solid var(--border-subtle)",
    transition: "all 0.2s ease",
  },
  postDate: {
    fontSize: 14,
    color: "var(--text-muted)",
    fontFamily: "'JetBrains Mono', monospace",
  },
  postContent: {
    flex: 1,
  },
  postTitle: {
    fontSize: 18,
    fontWeight: 600,
    marginBottom: 6,
    transition: "color 0.2s ease",
  },
  postExcerpt: {
    fontSize: 14,
    color: "var(--text-secondary)",
    lineHeight: 1.6,
  },
  postReadMore: {
    display: "flex",
    alignItems: "center",
    gap: 6,
    padding: "8px 16px",
    background: "transparent",
    border: "1px solid var(--border-default)",
    borderRadius: 6,
    color: "var(--text-secondary)",
    fontSize: 13,
    fontWeight: 500,
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
  loadMoreBtn: {
    width: "100%",
    padding: "16px",
    marginTop: 24,
    background: "transparent",
    border: "1px dashed var(--border-default)",
    borderRadius: 12,
    color: "var(--text-secondary)",
    fontSize: 14,
    fontWeight: 500,
    cursor: "pointer",
    transition: "all 0.2s ease",
  },
};
