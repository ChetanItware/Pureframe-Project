import postgres from 'postgres';
import 'dotenv/config';

const sql = postgres({
  host: process.env.DB_HOST,
  port: Number(process.env.DB_PORT),
  database: process.env.DB_NAME,
  username: process.env.DB_USER,
  password: process.env.DB_PASSWORD,
  onnotice: () => {},
  connect_timeout: 30
});

async function initDB() {
  try {
    console.log("⏳ Connecting to Database on port 5432...");
    await sql`
      CREATE TABLE IF NOT EXISTS extraction_requests (
        id SERIAL PRIMARY KEY,
        district TEXT,
        taluka TEXT,
        village TEXT,
        mutation_no TEXT,
        status TEXT DEFAULT 'processing',
        pdf_url TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        payment_id TEXT,
        doc_type TEXT DEFAULT 'FERFAR'
      )
    `;
    console.log("✅ PostgreSQL Table Ready (extraction_requests)");
  } catch (err) {
    console.error("❌ Database connection failed:", err.message);
  }
}
initDB();

export default sql;