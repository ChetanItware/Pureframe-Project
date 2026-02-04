import { Hono } from 'hono';
import { serveStatic } from 'hono/bun';
import postgres from 'postgres';
import amqp from 'amqplib';
import Razorpay from 'razorpay';
import crypto from 'crypto';
import { existsSync, mkdirSync } from 'fs';

const app = new Hono();
const RZP_ID = 'rzp_test_S9L06wUQS6uuhH';
const RZP_SECRET = 'WDfk3r0ydKkwcN3S8ZUXIekM';

// Ensure this connection string matches your local DB
const sql = postgres({
  host: '127.0.0.1',
  port: 5432,
  database: 'postgres',
  username: 'postgres',
  password: 'chetan'
});
const rzp = new Razorpay({ key_id: RZP_ID, key_secret: RZP_SECRET });

let channel: amqp.Channel;
async function initRabbitMQ() {
    try {
        const connection = await amqp.connect('amqp://localhost');
        channel = await connection.createChannel();
        await channel.assertQueue('task_queue', { durable: true });
        console.log("✅ RabbitMQ Connected and Queue 'task_queue' Ready");
    } catch (err) { 
        console.error("❌ RabbitMQ Failed. Is RabbitMQ Server running?", err); 
    }
}
initRabbitMQ();

if (!existsSync('./downloads')) mkdirSync('./downloads');

// Serve static files from the 'public' folder
app.use('*', serveStatic({ 
    root: './public',
    rewriteRequestPath: (path) => path === '/' ? '/index.html' : path 
}));

// Provide the file for download
app.get('/files/:filename', async (c) => {
    const filename = c.req.param('filename');
    const path = `./downloads/${filename}`;
    if (existsSync(path)) {
        return new Response(Bun.file(path), {
            headers: { 'Content-Type': 'application/pdf' }
        });
    }
    return c.text('Not Found', 404);
});

// Create Razorpay Order
app.post('/api/pay/create-order', async (c) => {
    try {
        const order = await rzp.orders.create({ 
            amount: 2000, 
            currency: "INR", 
            receipt: `rec_${Date.now()}` 
        });
        return c.json(order);
    } catch (e) { return c.json({ error: "Order creation failed" }, 500); }
});

// Verify Payment and Add to Queue
app.post('/api/request', async (c) => {
    const body = await c.req.json();
    const { district, taluka, village, mutation_no, razorpay_payment_id, razorpay_order_id, razorpay_signature } = body;

    // Verify Signature
    const hmac = crypto.createHmac('sha256', RZP_SECRET);
    hmac.update(razorpay_order_id + "|" + razorpay_payment_id);
    if (hmac.digest('hex') !== razorpay_signature) {
        return c.json({ error: "Payment verification failed" }, 401);
    }

    try {
        // 1. Insert into DB with status 'processing'
        const [request] = await sql`
            INSERT INTO extraction_requests (district, taluka, village, mutation_no, payment_id, status, doc_type)
            VALUES (${district}, ${taluka}, ${village}, ${mutation_no}, ${razorpay_payment_id}, 'processing', 'FERFAR')
            RETURNING id
        `;

        // 2. Send the job to RabbitMQ for worker.py to pick up
        const task = { 
            id: request.id, 
            district, 
            taluka, 
            village, 
            mutation_no, 
            doc_type: 'FERFAR' 
        };

        channel.sendToQueue('task_queue', Buffer.from(JSON.stringify(task)), { persistent: true });
        
        return c.json({ id: request.id });
    } catch (err) { 
        console.error(err);
        return c.json({ error: "Database or Queue error" }, 500); 
    }
});

// Polling Endpoint
app.get('/api/status/:id', async (c) => {
  const idParam = c.req.param('id');
  const id = Number(idParam);

  // 🛑 HARD GUARD
  if (!idParam || Number.isNaN(id)) {
    return c.json({ error: "Invalid request ID" }, 400);
  }

  try {
    const [record] = await sql`
      SELECT status, pdf_url
      FROM extraction_requests
      WHERE id = ${id}
    `;

    if (!record) {
      return c.json({ status: 'not_found' }, 404);
    }

    return c.json(record);
  } catch (e) {
    console.error("❌ Status fetch error:", e);
    return c.json({ error: "Database Error" }, 500);
  }
});

export default app;