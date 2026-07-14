--
-- PostgreSQL database dump
--

\restrict 3MJiVcZlHKSohguEhmPmmgMemXWmtddKAKpqmzei5TxNMZ5AdPNP26uebtOvzhM

-- Dumped from database version 16.14
-- Dumped by pg_dump version 16.14

SET statement_timeout = 0;
SET lock_timeout = 0;
SET idle_in_transaction_session_timeout = 0;
SET client_encoding = 'UTF8';
SET standard_conforming_strings = on;
SELECT pg_catalog.set_config('search_path', '', false);
SET check_function_bodies = false;
SET xmloption = content;
SET client_min_messages = warning;
SET row_security = off;

--
-- Name: uuid-ossp; Type: EXTENSION; Schema: -; Owner: -
--

CREATE EXTENSION IF NOT EXISTS "uuid-ossp" WITH SCHEMA public;


--
-- Name: EXTENSION "uuid-ossp"; Type: COMMENT; Schema: -; Owner: -
--

COMMENT ON EXTENSION "uuid-ossp" IS 'generate universally unique identifiers (UUIDs)';


SET default_tablespace = '';

SET default_table_access_method = heap;

--
-- Name: account_instruments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_instruments (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    account_id uuid NOT NULL,
    instrument_id uuid NOT NULL,
    broker_symbol character varying(100) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: account_strategies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.account_strategies (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    account_id uuid NOT NULL,
    strategy_id uuid NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: alerts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.alerts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    instrument character varying(50) NOT NULL,
    condition_type character varying(50) NOT NULL,
    condition_value jsonb NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    triggered_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: audit_log; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.audit_log (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid,
    event_type character varying(100) NOT NULL,
    details jsonb,
    ip_address inet,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: autopilot_states; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.autopilot_states (
    account_id uuid NOT NULL,
    enabled boolean DEFAULT false NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: backtest_results; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_results (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    strategy_id uuid,
    user_id uuid,
    config jsonb NOT NULL,
    win_rate numeric(5,4),
    max_drawdown numeric(30,8),
    sharpe_ratio numeric(20,4),
    profit_factor numeric(20,4),
    expectancy numeric(30,8),
    total_trades integer,
    trade_results jsonb,
    status character varying(20) DEFAULT 'pending'::character varying NOT NULL,
    error_message text,
    winning_trades integer,
    losing_trades integer,
    gross_profit numeric(30,8),
    gross_loss numeric(30,8),
    net_profit numeric(30,8),
    equity_curve jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    average_rr numeric(10,2)
);


--
-- Name: backtest_trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.backtest_trades (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    backtest_result_id uuid NOT NULL,
    signal_id character varying(255) NOT NULL,
    direction character varying(10) NOT NULL,
    entry_price numeric(18,8) NOT NULL,
    exit_price numeric(18,8) NOT NULL,
    stop_loss numeric(18,8),
    take_profit numeric(18,8),
    position_size numeric(30,8) NOT NULL,
    profit_loss numeric(30,8) NOT NULL,
    entry_time timestamp with time zone NOT NULL,
    exit_time timestamp with time zone NOT NULL,
    trade_index integer NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    reward_risk numeric(10,2),
    initial_stop_loss numeric(18,8),
    balance_before numeric(30,2),
    balance_after numeric(30,2)
);


--
-- Name: candles; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.candles (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    instrument character varying(50) NOT NULL,
    timeframe character varying(10) NOT NULL,
    open double precision NOT NULL,
    high double precision NOT NULL,
    low double precision NOT NULL,
    close double precision NOT NULL,
    volume double precision DEFAULT '0'::double precision NOT NULL,
    "timestamp" timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    completed boolean DEFAULT false NOT NULL
);


--
-- Name: instruments; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.instruments (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    symbol character varying(50) NOT NULL,
    display_name character varying(100) NOT NULL,
    type character varying(20) NOT NULL,
    is_active boolean DEFAULT true NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    deriv_symbol character varying(50),
    contract_size numeric(16,6) DEFAULT 1 NOT NULL,
    pip_size numeric(16,8) DEFAULT 0.01 NOT NULL,
    pip_value numeric(16,6) DEFAULT 1 NOT NULL,
    min_lot numeric(10,4) DEFAULT 0.01 NOT NULL,
    lot_step numeric(10,4) DEFAULT 0.01 NOT NULL,
    leverage integer DEFAULT 100 NOT NULL,
    category character varying(20),
    preferred_provider character varying(20)
);


--
-- Name: kill_switch; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.kill_switch (
    id integer DEFAULT 1 NOT NULL,
    is_active boolean DEFAULT false NOT NULL,
    activated_by uuid,
    activated_at timestamp with time zone,
    deactivated_at timestamp with time zone
);


--
-- Name: portfolio_snapshots; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.portfolio_snapshots (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    account_id uuid,
    equity numeric(18,8) NOT NULL,
    balance numeric(18,8) NOT NULL,
    unrealized_pnl numeric(18,8) NOT NULL,
    open_positions integer NOT NULL,
    margin numeric(18,8) DEFAULT '0'::numeric,
    free_margin numeric(18,8) DEFAULT '0'::numeric,
    leverage integer DEFAULT 0,
    snapshot_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: positions; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.positions (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    account_id uuid,
    trade_id uuid,
    instrument character varying(50) NOT NULL,
    direction character varying(10) NOT NULL,
    entry_price numeric(18,8) NOT NULL,
    current_price numeric(18,8),
    position_size numeric(18,8) NOT NULL,
    unrealized_pnl numeric(18,8),
    opened_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: reconciliation_configs; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reconciliation_configs (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    account_id uuid,
    reconciliation_interval_seconds integer DEFAULT 60 NOT NULL,
    balance_drift_threshold numeric(18,2) DEFAULT '10'::numeric NOT NULL,
    equity_drift_threshold numeric(18,2) DEFAULT '50'::numeric NOT NULL,
    position_size_drift_threshold numeric(18,4) DEFAULT 0.01 NOT NULL,
    auto_correct_phantom_positions boolean DEFAULT false NOT NULL,
    auto_correct_missing_positions boolean DEFAULT false NOT NULL,
    auto_correct_balance_drift boolean DEFAULT false NOT NULL,
    escalation_cycle_count integer DEFAULT 3 NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: reconciliation_reports; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.reconciliation_reports (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    account_id uuid NOT NULL,
    cycle_timestamp timestamp with time zone NOT NULL,
    discrepancies jsonb NOT NULL,
    auto_corrections_applied jsonb NOT NULL,
    broker_state_snapshot jsonb NOT NULL,
    local_state_snapshot jsonb NOT NULL,
    duration_ms integer NOT NULL,
    status character varying(30) NOT NULL,
    error_message text,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: refresh_tokens; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.refresh_tokens (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    token_hash character varying(255) NOT NULL,
    expires_at timestamp with time zone NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: signals; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.signals (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    instrument character varying(50) NOT NULL,
    direction character varying(10) NOT NULL,
    entry_price numeric(18,8) NOT NULL,
    stop_loss numeric(18,8) NOT NULL,
    take_profit numeric(18,8) NOT NULL,
    position_size numeric(18,8) NOT NULL,
    confidence_score numeric(5,4) NOT NULL,
    timeframe character varying(10) NOT NULL,
    order_block_id character varying(255),
    strategy_id uuid,
    mode character varying(20) NOT NULL,
    metadata jsonb,
    created_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: strategies; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.strategies (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    name character varying(255) NOT NULL,
    algorithm character varying(100) DEFAULT 'ict_order_block'::character varying NOT NULL,
    config jsonb NOT NULL,
    created_by uuid,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL,
    enabled boolean DEFAULT true NOT NULL
);


--
-- Name: trades; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trades (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    signal_id uuid,
    account_id uuid,
    broker_order_id bigint,
    direction character varying(10) NOT NULL,
    entry_price numeric(18,8),
    exit_price numeric(18,8),
    fill_price numeric(18,8),
    position_size numeric(18,8) NOT NULL,
    profit_loss numeric(18,8),
    execution_latency_ms integer,
    slippage numeric(18,8),
    spread_at_execution numeric(18,8),
    status character varying(20) NOT NULL,
    rejection_reason text,
    opened_at timestamp with time zone,
    closed_at timestamp with time zone,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    instrument character varying(50)
);


--
-- Name: trading_accounts; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trading_accounts (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    metaapi_account_id character varying(255),
    label character varying(255),
    is_active boolean DEFAULT true NOT NULL,
    mt5_login character varying(50),
    mt5_server character varying(255),
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    account_kind character varying(20) DEFAULT 'personal'::character varying NOT NULL,
    broker_provider character varying(20),
    prop_firm_name character varying(100),
    prop_max_daily_loss_pct numeric(5,2),
    prop_max_total_drawdown_pct numeric(5,2),
    prop_profit_target_pct numeric(5,2),
    deriv_api_token character varying(500),
    deriv_login_id character varying(50)
);


--
-- Name: trading_events; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.trading_events (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    event_type character varying(50) NOT NULL,
    aggregate_id character varying(255) NOT NULL,
    sequence_number integer NOT NULL,
    correlation_id character varying(255),
    payload jsonb NOT NULL,
    context_snapshot jsonb,
    source_service character varying(50) NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    schema_version integer DEFAULT 1 NOT NULL
);


--
-- Name: users; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.users (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    email character varying(255) NOT NULL,
    password_hash character varying(255) NOT NULL,
    role character varying(50) DEFAULT 'trader'::character varying NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: watchlists; Type: TABLE; Schema: public; Owner: -
--

CREATE TABLE public.watchlists (
    id uuid DEFAULT public.uuid_generate_v4() NOT NULL,
    user_id uuid NOT NULL,
    name character varying(255) NOT NULL,
    instruments jsonb DEFAULT '[]'::jsonb NOT NULL,
    created_at timestamp with time zone DEFAULT now() NOT NULL,
    updated_at timestamp with time zone DEFAULT now() NOT NULL
);


--
-- Name: signals PK_04eeac09c09b65bc55c628c101d; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signals
    ADD CONSTRAINT "PK_04eeac09c09b65bc55c628c101d" PRIMARY KEY (id);


--
-- Name: audit_log PK_07fefa57f7f5ab8fc3f52b3ed0b; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT "PK_07fefa57f7f5ab8fc3f52b3ed0b" PRIMARY KEY (id);


--
-- Name: reconciliation_reports PK_12ad01f5998711edaac25681bf4; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_reports
    ADD CONSTRAINT "PK_12ad01f5998711edaac25681bf4" PRIMARY KEY (id);


--
-- Name: positions PK_17e4e62ccd5749b289ae3fae6f3; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT "PK_17e4e62ccd5749b289ae3fae6f3" PRIMARY KEY (id);


--
-- Name: kill_switch PK_1a2701be7d55428ea2c8f13cf7f; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kill_switch
    ADD CONSTRAINT "PK_1a2701be7d55428ea2c8f13cf7f" PRIMARY KEY (id);


--
-- Name: trading_events PK_2d42a6d48d5d8ded74ef7bb4742; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_events
    ADD CONSTRAINT "PK_2d42a6d48d5d8ded74ef7bb4742" PRIMARY KEY (id);


--
-- Name: instruments PK_44d772c3199b38559c5fb666eb6; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT "PK_44d772c3199b38559c5fb666eb6" PRIMARY KEY (id);


--
-- Name: portfolio_snapshots PK_46c13ef40300b3a6d379488f53a; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_snapshots
    ADD CONSTRAINT "PK_46c13ef40300b3a6d379488f53a" PRIMARY KEY (id);


--
-- Name: candles PK_51487d0946f705bd3df19d2f04e; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candles
    ADD CONSTRAINT "PK_51487d0946f705bd3df19d2f04e" PRIMARY KEY (id);


--
-- Name: alerts PK_60f895662df096bfcdfab7f4b96; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT "PK_60f895662df096bfcdfab7f4b96" PRIMARY KEY (id);


--
-- Name: refresh_tokens PK_7d8bee0204106019488c4c50ffa; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT "PK_7d8bee0204106019488c4c50ffa" PRIMARY KEY (id);


--
-- Name: account_instruments PK_97c1632b30d5fa3c720ef6edc28; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_instruments
    ADD CONSTRAINT "PK_97c1632b30d5fa3c720ef6edc28" PRIMARY KEY (id);


--
-- Name: strategies PK_9a0d363ddf5b40d080147363238; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategies
    ADD CONSTRAINT "PK_9a0d363ddf5b40d080147363238" PRIMARY KEY (id);


--
-- Name: users PK_a3ffb1c0c8416b9fc6f907b7433; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT "PK_a3ffb1c0c8416b9fc6f907b7433" PRIMARY KEY (id);


--
-- Name: reconciliation_configs PK_a94757248fc6a452fcbf588b34f; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_configs
    ADD CONSTRAINT "PK_a94757248fc6a452fcbf588b34f" PRIMARY KEY (id);


--
-- Name: watchlists PK_aa3c717b50a10f7a435c65eda5a; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT "PK_aa3c717b50a10f7a435c65eda5a" PRIMARY KEY (id);


--
-- Name: trading_accounts PK_ba41c42a3ecab326ed3b04d74d9; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_accounts
    ADD CONSTRAINT "PK_ba41c42a3ecab326ed3b04d74d9" PRIMARY KEY (id);


--
-- Name: trades PK_c6d7c36a837411ba5194dc58595; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trades
    ADD CONSTRAINT "PK_c6d7c36a837411ba5194dc58595" PRIMARY KEY (id);


--
-- Name: backtest_results PK_c8ecbe64505b329c98f142673e3; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_results
    ADD CONSTRAINT "PK_c8ecbe64505b329c98f142673e3" PRIMARY KEY (id);


--
-- Name: account_strategies PK_e90692d00bfcfecd45f8584d161; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_strategies
    ADD CONSTRAINT "PK_e90692d00bfcfecd45f8584d161" PRIMARY KEY (id);


--
-- Name: backtest_trades PK_f167e5734703aebc8cd49b4f4b0; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT "PK_f167e5734703aebc8cd49b4f4b0" PRIMARY KEY (id);


--
-- Name: autopilot_states PK_fe417365cec126e09a2a734b846; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.autopilot_states
    ADD CONSTRAINT "PK_fe417365cec126e09a2a734b846" PRIMARY KEY (account_id);


--
-- Name: account_instruments UQ_0710a0dad71299a6c9214246bb4; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_instruments
    ADD CONSTRAINT "UQ_0710a0dad71299a6c9214246bb4" UNIQUE (account_id, instrument_id);


--
-- Name: account_strategies UQ_2e292dbdbc979bcce7320bad8b1; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_strategies
    ADD CONSTRAINT "UQ_2e292dbdbc979bcce7320bad8b1" UNIQUE (account_id, strategy_id);


--
-- Name: candles UQ_4992600f61da19e5735e19ff2ba; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.candles
    ADD CONSTRAINT "UQ_4992600f61da19e5735e19ff2ba" UNIQUE (instrument, timeframe, "timestamp");


--
-- Name: trading_accounts UQ_5ec250f19609821f015023e1b0b; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_accounts
    ADD CONSTRAINT "UQ_5ec250f19609821f015023e1b0b" UNIQUE (user_id, mt5_login, mt5_server);


--
-- Name: reconciliation_configs UQ_882fb9c8336a8c8c76c4549534b; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_configs
    ADD CONSTRAINT "UQ_882fb9c8336a8c8c76c4549534b" UNIQUE (account_id);


--
-- Name: instruments UQ_8bd2da22a1ed32dced42f6a4f24; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.instruments
    ADD CONSTRAINT "UQ_8bd2da22a1ed32dced42f6a4f24" UNIQUE (symbol);


--
-- Name: users UQ_97672ac88f789774dd47f7c8be3; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.users
    ADD CONSTRAINT "UQ_97672ac88f789774dd47f7c8be3" UNIQUE (email);


--
-- Name: trading_events uq_aggregate_sequence; Type: CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_events
    ADD CONSTRAINT uq_aggregate_sequence UNIQUE (aggregate_id, sequence_number);


--
-- Name: idx_alerts_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_alerts_user_id ON public.alerts USING btree (user_id);


--
-- Name: idx_candles_instrument_timeframe_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_candles_instrument_timeframe_timestamp ON public.candles USING btree (instrument, timeframe, "timestamp");


--
-- Name: idx_portfolio_snapshots_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portfolio_snapshots_account_id ON public.portfolio_snapshots USING btree (account_id);


--
-- Name: idx_portfolio_snapshots_snapshot_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_portfolio_snapshots_snapshot_at ON public.portfolio_snapshots USING btree (snapshot_at);


--
-- Name: idx_positions_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_positions_account_id ON public.positions USING btree (account_id);


--
-- Name: idx_reconciliation_reports_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reconciliation_reports_account_id ON public.reconciliation_reports USING btree (account_id);


--
-- Name: idx_reconciliation_reports_cycle_timestamp; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reconciliation_reports_cycle_timestamp ON public.reconciliation_reports USING btree (cycle_timestamp);


--
-- Name: idx_reconciliation_reports_status; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_reconciliation_reports_status ON public.reconciliation_reports USING btree (status);


--
-- Name: idx_signals_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signals_created_at ON public.signals USING btree (created_at);


--
-- Name: idx_signals_strategy_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_signals_strategy_id ON public.signals USING btree (strategy_id);


--
-- Name: idx_trades_account_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trades_account_id ON public.trades USING btree (account_id);


--
-- Name: idx_trades_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trades_created_at ON public.trades USING btree (created_at);


--
-- Name: idx_trades_signal_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trades_signal_id ON public.trades USING btree (signal_id);


--
-- Name: idx_trading_events_aggregate_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trading_events_aggregate_id ON public.trading_events USING btree (aggregate_id);


--
-- Name: idx_trading_events_correlation_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trading_events_correlation_id ON public.trading_events USING btree (correlation_id);


--
-- Name: idx_trading_events_created_at; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trading_events_created_at ON public.trading_events USING btree (created_at);


--
-- Name: idx_trading_events_event_type; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trading_events_event_type ON public.trading_events USING btree (event_type);


--
-- Name: idx_trading_events_source_service; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_trading_events_source_service ON public.trading_events USING btree (source_service);


--
-- Name: idx_watchlists_user_id; Type: INDEX; Schema: public; Owner: -
--

CREATE INDEX idx_watchlists_user_id ON public.watchlists USING btree (user_id);


--
-- Name: strategies FK_0118cd40c3be6124f2d5f8ad3c4; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.strategies
    ADD CONSTRAINT "FK_0118cd40c3be6124f2d5f8ad3c4" FOREIGN KEY (created_by) REFERENCES public.users(id);


--
-- Name: positions FK_0ed8ab557a8fb4125b8948bd79b; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT "FK_0ed8ab557a8fb4125b8948bd79b" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id);


--
-- Name: account_strategies FK_16e62d1acdec19e9718c0684d3d; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_strategies
    ADD CONSTRAINT "FK_16e62d1acdec19e9718c0684d3d" FOREIGN KEY (strategy_id) REFERENCES public.strategies(id) ON DELETE CASCADE;


--
-- Name: backtest_results FK_1df4efd1e73fcc6e70558384071; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_results
    ADD CONSTRAINT "FK_1df4efd1e73fcc6e70558384071" FOREIGN KEY (user_id) REFERENCES public.users(id);


--
-- Name: trades FK_27087c82d355a714f16922dd52d; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trades
    ADD CONSTRAINT "FK_27087c82d355a714f16922dd52d" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id);


--
-- Name: portfolio_snapshots FK_2e6a8ffa5cd8ba1ff5838f65537; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.portfolio_snapshots
    ADD CONSTRAINT "FK_2e6a8ffa5cd8ba1ff5838f65537" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id);


--
-- Name: refresh_tokens FK_3ddc983c5f7bcf132fd8732c3f4; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.refresh_tokens
    ADD CONSTRAINT "FK_3ddc983c5f7bcf132fd8732c3f4" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: watchlists FK_3e8bccad3dcd75fa977892c54bb; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.watchlists
    ADD CONSTRAINT "FK_3e8bccad3dcd75fa977892c54bb" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: trading_accounts FK_56a2666c71c5995fd725596ca1e; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trading_accounts
    ADD CONSTRAINT "FK_56a2666c71c5995fd725596ca1e" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: backtest_trades FK_839b9f584235ece4429e90c3f8c; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_trades
    ADD CONSTRAINT "FK_839b9f584235ece4429e90c3f8c" FOREIGN KEY (backtest_result_id) REFERENCES public.backtest_results(id) ON DELETE CASCADE;


--
-- Name: account_strategies FK_863381c7152efaf8186606d0f80; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_strategies
    ADD CONSTRAINT "FK_863381c7152efaf8186606d0f80" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id) ON DELETE CASCADE;


--
-- Name: reconciliation_configs FK_882fb9c8336a8c8c76c4549534b; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_configs
    ADD CONSTRAINT "FK_882fb9c8336a8c8c76c4549534b" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id) ON DELETE CASCADE;


--
-- Name: positions FK_8ef277c0b880c92a2e64c7ae641; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.positions
    ADD CONSTRAINT "FK_8ef277c0b880c92a2e64c7ae641" FOREIGN KEY (trade_id) REFERENCES public.trades(id);


--
-- Name: signals FK_9ec1d3272c715761109633b145f; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.signals
    ADD CONSTRAINT "FK_9ec1d3272c715761109633b145f" FOREIGN KEY (strategy_id) REFERENCES public.strategies(id);


--
-- Name: backtest_results FK_b783ab9ea0264d3a07e54020273; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.backtest_results
    ADD CONSTRAINT "FK_b783ab9ea0264d3a07e54020273" FOREIGN KEY (strategy_id) REFERENCES public.strategies(id);


--
-- Name: kill_switch FK_bb501680b03d0e13e5a0f966574; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.kill_switch
    ADD CONSTRAINT "FK_bb501680b03d0e13e5a0f966574" FOREIGN KEY (activated_by) REFERENCES public.users(id);


--
-- Name: audit_log FK_cb11bd5b662431ea0ac455a27d7; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.audit_log
    ADD CONSTRAINT "FK_cb11bd5b662431ea0ac455a27d7" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE SET NULL;


--
-- Name: account_instruments FK_cb63dfcdf830d8d1d92581a0ca5; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_instruments
    ADD CONSTRAINT "FK_cb63dfcdf830d8d1d92581a0ca5" FOREIGN KEY (instrument_id) REFERENCES public.instruments(id) ON DELETE CASCADE;


--
-- Name: account_instruments FK_d29f5a7a17e63c5cdfab1ee5718; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.account_instruments
    ADD CONSTRAINT "FK_d29f5a7a17e63c5cdfab1ee5718" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id) ON DELETE CASCADE;


--
-- Name: trades FK_dd6159b076e6457733d0373c835; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.trades
    ADD CONSTRAINT "FK_dd6159b076e6457733d0373c835" FOREIGN KEY (signal_id) REFERENCES public.signals(id);


--
-- Name: alerts FK_f1eba840c1761991f142affee66; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.alerts
    ADD CONSTRAINT "FK_f1eba840c1761991f142affee66" FOREIGN KEY (user_id) REFERENCES public.users(id) ON DELETE CASCADE;


--
-- Name: reconciliation_reports FK_f23e45b91091819e0aa04159516; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.reconciliation_reports
    ADD CONSTRAINT "FK_f23e45b91091819e0aa04159516" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id) ON DELETE CASCADE;


--
-- Name: autopilot_states FK_fe417365cec126e09a2a734b846; Type: FK CONSTRAINT; Schema: public; Owner: -
--

ALTER TABLE ONLY public.autopilot_states
    ADD CONSTRAINT "FK_fe417365cec126e09a2a734b846" FOREIGN KEY (account_id) REFERENCES public.trading_accounts(id) ON DELETE CASCADE;


--
-- PostgreSQL database dump complete
--

\unrestrict 3MJiVcZlHKSohguEhmPmmgMemXWmtddKAKpqmzei5TxNMZ5AdPNP26uebtOvzhM

