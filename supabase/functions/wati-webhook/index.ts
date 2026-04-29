import { createClient } from "@supabase/supabase-js";

const supabaseUrl = Deno.env.get("SUPABASE_URL")!;
const supabaseServiceKey = Deno.env.get("SUPABASE_SERVICE_ROLE_KEY")!;

const supabase = createClient(supabaseUrl, supabaseServiceKey);

interface WebhookResult {
  status: string;
  log_id?: string;
  staging_id?: string;
  promotion?: Record<string, unknown>;
  error?: string;
}

Deno.serve(async (req: Request): Promise<Response> => {
  if (req.method !== "POST") {
    return new Response(JSON.stringify({ error: "Method not allowed" }), {
      status: 405,
      headers: { "Content-Type": "application/json" },
    });
  }

  const url = new URL(req.url);
  const tenantIdParam = url.searchParams.get("tenant_id");
  const secretParam = url.searchParams.get("secret");

  if (!tenantIdParam) {
    return new Response(
      JSON.stringify({ error: "Missing tenant_id query parameter" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const tenantId = parseInt(tenantIdParam, 10);
  if (isNaN(tenantId)) {
    return new Response(
      JSON.stringify({ error: "Invalid tenant_id" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  // Validate tenant and webhook secret
  const { data: connector, error: connectorError } = await supabase
    .from("tenant_connectors")
    .select("connector_id, webhook_secret")
    .eq("tenant_id", tenantId)
    .eq("provider", "wati")
    .eq("is_active", true)
    .limit(1)
    .single();

  if (connectorError || !connector) {
    return new Response(
      JSON.stringify({ error: "Unknown or inactive tenant" }),
      { status: 404, headers: { "Content-Type": "application/json" } },
    );
  }

  if (connector.webhook_secret) {
    if (secretParam !== connector.webhook_secret) {
      return new Response(
        JSON.stringify({ error: "Invalid webhook secret" }),
        { status: 401, headers: { "Content-Type": "application/json" } },
      );
    }
  }

  // Parse the incoming payload
  let payload: Record<string, unknown>;
  try {
    payload = await req.json();
  } catch {
    return new Response(
      JSON.stringify({ error: "Invalid JSON body" }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  const result: WebhookResult = { status: "processing" };

  // Extract event type and message fields from Wati payload
  const eventType = (payload.eventType as string) || "message";
  const watiMessageId = String(
    payload.id || payload.whatsappMessageId || "",
  );
  const waId = String(payload.waId || "");

  if (!watiMessageId || !waId) {
    return new Response(
      JSON.stringify({
        error: "Missing required fields: id/whatsappMessageId and waId",
      }),
      { status: 400, headers: { "Content-Type": "application/json" } },
    );
  }

  try {
    // Step 1: Log raw payload to ops.webhook_logs
    const { data: logRow, error: logError } = await supabase
      .schema("ops")
      .from("webhook_logs")
      .insert({
        tenant_id: tenantId,
        event_type: eventType,
        payload: payload,
      })
      .select("log_id")
      .single();

    if (logError) throw new Error(`webhook_logs insert: ${logError.message}`);
    result.log_id = logRow.log_id;

    // Step 2: Upsert message into staging.messages_raw_data
    // conversation_id is the raw waId — normalize_phone() is applied in SQL
    const { data: stagingRow, error: stagingError } = await supabase
      .schema("staging")
      .from("messages_raw_data")
      .upsert(
        {
          tenant_id: tenantId,
          wati_message_id: watiMessageId,
          conversation_id: waId,
          raw_data: payload,
        },
        { onConflict: "tenant_id,wati_message_id" },
      )
      .select("id")
      .single();

    if (stagingError)
      throw new Error(`staging upsert: ${stagingError.message}`);
    result.staging_id = stagingRow.id;

    // Step 3: Promote to canonical via single-message RPC
    const { data: promotion, error: rpcError } = await supabase.rpc(
      "process_webhook_message_to_canonical",
      { p_staging_id: stagingRow.id },
    );

    if (rpcError)
      throw new Error(`promotion RPC: ${rpcError.message}`);
    result.promotion = promotion;

    // Determine outcome for logging
    const promotionStatus = (promotion as Record<string, unknown>)?.status;
    const messagesInserted =
      promotionStatus === "success" || promotionStatus === "duplicate" ? 1 : 0;
    const processingStatus =
      promotionStatus === "skipped" ? "skipped" : "success";

    // Step 4: Record processing outcome
    await supabase
      .schema("ops")
      .from("webhook_processing_log")
      .insert({
        log_id: logRow.log_id,
        tenant_id: tenantId,
        status: processingStatus,
        messages_inserted: messagesInserted,
      });

    // Step 5: Update aggregated health status (atomic counter increment via RPC)
    await supabase.rpc("update_webhook_status", {
      p_tenant_id: tenantId,
      p_status: processingStatus,
    });

    // Mark webhook log as processed
    await supabase
      .schema("ops")
      .from("webhook_logs")
      .update({ processed: true })
      .eq("log_id", logRow.log_id);

    result.status = processingStatus;

    return new Response(JSON.stringify(result), {
      status: 200,
      headers: { "Content-Type": "application/json" },
    });
  } catch (err) {
    const errorMessage = err instanceof Error ? err.message : String(err);
    result.status = "failed";
    result.error = errorMessage;

    // Best-effort: record failure in processing log
    if (result.log_id) {
      await supabase
        .schema("ops")
        .from("webhook_processing_log")
        .insert({
          log_id: result.log_id,
          tenant_id: tenantId,
          status: "failed",
          error: errorMessage,
        })
        .then(() => {});

      await supabase
        .schema("ops")
        .from("webhook_logs")
        .update({ processed: true, error: errorMessage })
        .eq("log_id", result.log_id)
        .then(() => {});
    }

    // Update health status with failure (atomic counter increment via RPC)
    await supabase
      .rpc("update_webhook_status", {
        p_tenant_id: tenantId,
        p_status: "failed",
      })
      .then(() => {});

    return new Response(JSON.stringify(result), {
      status: 500,
      headers: { "Content-Type": "application/json" },
    });
  }
});
