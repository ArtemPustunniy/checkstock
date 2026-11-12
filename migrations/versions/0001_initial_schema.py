"""Начальная схема: товары, ФФ, остатки, документы, журнал проводок, ключи идемпотентности.

Revision ID: 0001
Revises:
"""

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

# Допустимые сочетания типа документа, статуса и складов.
KIND_SHAPE = """
    (kind = 'receipt' AND status = 'done'
        AND source_warehouse_id IS NULL AND dest_warehouse_id IS NOT NULL)
    OR (kind = 'transfer' AND status IN ('in_transit', 'received')
        AND source_warehouse_id IS NOT NULL AND dest_warehouse_id IS NOT NULL
        AND source_warehouse_id <> dest_warehouse_id)
    OR (kind = 'fbo' AND status IN ('in_transit', 'accepted')
        AND source_warehouse_id IS NOT NULL AND dest_warehouse_id IS NULL)
    OR (kind = 'fbs' AND status IN ('reserved', 'shipped', 'delivered', 'cancelled')
        AND source_warehouse_id IS NOT NULL AND dest_warehouse_id IS NULL)
"""


def upgrade() -> None:
    op.create_table(
        "idempotency_keys",
        sa.Column("key", sa.String(length=100), nullable=False),
        sa.Column("endpoint", sa.String(length=50), nullable=False),
        sa.Column("request_hash", sa.String(length=64), nullable=False),
        sa.Column("status_code", sa.Integer(), server_default="0", nullable=False),
        sa.Column("response", postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.PrimaryKeyConstraint("key", name=op.f("pk_idempotency_keys")),
    )
    op.create_table(
        "products",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("sku", sa.String(length=64), nullable=False),
        sa.Column("name", sa.String(length=200), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_products")),
        sa.UniqueConstraint("sku", name=op.f("uq_products_sku")),
    )
    op.create_table(
        "warehouses",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("deleted_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_warehouses")),
    )
    op.create_index(
        "uq_warehouses_active_name",
        "warehouses",
        [sa.text("lower(name)")],
        unique=True,
        postgresql_where=sa.text("deleted_at IS NULL"),
    )
    op.create_table(
        "documents",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column(
            "kind",
            sa.Enum(
                "receipt",
                "transfer",
                "fbo",
                "fbs",
                name="doc_kind",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column(
            "status",
            sa.Enum(
                "done",
                "in_transit",
                "received",
                "accepted",
                "reserved",
                "shipped",
                "delivered",
                "cancelled",
                name="doc_status",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column("source_warehouse_id", sa.Integer(), nullable=True),
        sa.Column("dest_warehouse_id", sa.Integer(), nullable=True),
        sa.Column("reference", sa.String(length=100), nullable=True),
        sa.Column("destination", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(KIND_SHAPE, name=op.f("ck_documents_kind_shape")),
        sa.CheckConstraint("qty > 0", name=op.f("ck_documents_qty_positive")),
        sa.ForeignKeyConstraint(
            ["dest_warehouse_id"], ["warehouses.id"], name=op.f("fk_documents_dest_warehouse_id_warehouses")
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_documents_product_id_products")),
        sa.ForeignKeyConstraint(
            ["source_warehouse_id"], ["warehouses.id"], name=op.f("fk_documents_source_warehouse_id_warehouses")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_documents")),
    )
    op.create_index("ix_documents_kind_status", "documents", ["kind", "status"], unique=False)
    op.create_table(
        "stock_balances",
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("available", sa.Integer(), server_default="0", nullable=False),
        sa.Column("reserved", sa.Integer(), server_default="0", nullable=False),
        sa.CheckConstraint("available >= 0", name=op.f("ck_stock_balances_available_non_negative")),
        sa.CheckConstraint("reserved >= 0", name=op.f("ck_stock_balances_reserved_non_negative")),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_stock_balances_product_id_products")),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], name=op.f("fk_stock_balances_warehouse_id_warehouses")
        ),
        sa.PrimaryKeyConstraint("product_id", "warehouse_id", name=op.f("pk_stock_balances")),
    )
    op.create_table(
        "warehouse_events",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("warehouse_id", sa.Integer(), nullable=False),
        sa.Column("event", sa.String(length=16), nullable=False),
        sa.Column("old_name", sa.String(length=100), nullable=True),
        sa.Column("new_name", sa.String(length=100), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(
            ["warehouse_id"], ["warehouses.id"], name=op.f("fk_warehouse_events_warehouse_id_warehouses")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_warehouse_events")),
    )
    op.create_index(op.f("ix_warehouse_events_warehouse_id"), "warehouse_events", ["warehouse_id"], unique=False)
    op.create_table(
        "movements",
        sa.Column("id", sa.BigInteger(), nullable=False),
        sa.Column("document_id", sa.Integer(), nullable=False),
        sa.Column(
            "action",
            sa.Enum(
                "receipt",
                "transfer_send",
                "transfer_receive",
                "fbo_ship",
                "fbo_accept",
                "fbs_reserve",
                "fbs_ship",
                "fbs_cancel",
                "fbs_deliver",
                name="movement_action",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("product_id", sa.Integer(), nullable=False),
        sa.Column("qty", sa.Integer(), nullable=False),
        sa.Column(
            "from_bucket",
            sa.Enum(
                "supplier",
                "available",
                "reserved",
                "transit_ff",
                "transit_fbo",
                "transit_fbs",
                "marketplace",
                "client",
                name="from_bucket",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("from_warehouse_id", sa.Integer(), nullable=True),
        sa.Column(
            "to_bucket",
            sa.Enum(
                "supplier",
                "available",
                "reserved",
                "transit_ff",
                "transit_fbo",
                "transit_fbs",
                "marketplace",
                "client",
                name="to_bucket",
                native_enum=False,
                create_constraint=True,
                length=20,
            ),
            nullable=False,
        ),
        sa.Column("to_warehouse_id", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.CheckConstraint(
            "(from_bucket IN ('available', 'reserved')) = (from_warehouse_id IS NOT NULL)",
            name=op.f("ck_movements_from_warehouse_matches_bucket"),
        ),
        sa.CheckConstraint(
            "(to_bucket IN ('available', 'reserved')) = (to_warehouse_id IS NOT NULL)",
            name=op.f("ck_movements_to_warehouse_matches_bucket"),
        ),
        sa.CheckConstraint("qty > 0", name=op.f("ck_movements_qty_positive")),
        sa.ForeignKeyConstraint(["document_id"], ["documents.id"], name=op.f("fk_movements_document_id_documents")),
        sa.ForeignKeyConstraint(
            ["from_warehouse_id"], ["warehouses.id"], name=op.f("fk_movements_from_warehouse_id_warehouses")
        ),
        sa.ForeignKeyConstraint(["product_id"], ["products.id"], name=op.f("fk_movements_product_id_products")),
        sa.ForeignKeyConstraint(
            ["to_warehouse_id"], ["warehouses.id"], name=op.f("fk_movements_to_warehouse_id_warehouses")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_movements")),
    )
    op.create_index(op.f("ix_movements_document_id"), "movements", ["document_id"], unique=False)
    op.create_index(op.f("ix_movements_product_id"), "movements", ["product_id"], unique=False)


def downgrade() -> None:
    op.drop_index(op.f("ix_movements_product_id"), table_name="movements")
    op.drop_index(op.f("ix_movements_document_id"), table_name="movements")
    op.drop_table("movements")
    op.drop_index(op.f("ix_warehouse_events_warehouse_id"), table_name="warehouse_events")
    op.drop_table("warehouse_events")
    op.drop_table("stock_balances")
    op.drop_index("ix_documents_kind_status", table_name="documents")
    op.drop_table("documents")
    op.drop_index("uq_warehouses_active_name", table_name="warehouses", postgresql_where=sa.text("deleted_at IS NULL"))
    op.drop_table("warehouses")
    op.drop_table("products")
    op.drop_table("idempotency_keys")
