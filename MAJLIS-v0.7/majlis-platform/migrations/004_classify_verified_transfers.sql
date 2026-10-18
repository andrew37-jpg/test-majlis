-- Classify only v0.5 transfers proven by the matching audit entry recorded by v0.5.
-- No old customer DB mapping is inferred, and arbitrary matching notes alone are insufficient.
UPDATE events e SET kind='transfer'
WHERE e.kind='status'
  AND e.source='manager'
  AND e.note='Заказ привязан к вашему кабинету'
  AND EXISTS (
      SELECT 1 FROM audit_log a
      WHERE a.order_id=e.order_id AND a.action='transfer'
        AND a.created_at=e.created_at
        AND a.actor_id IS NOT DISTINCT FROM e.actor_id
        AND a.after_data->>'customer_generation'=e.customer_generation::text
  );
