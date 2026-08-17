"""Estado das consultas em SQLite.

O SQLite e a fonte da verdade, nao a planilha. Assim o worker pode ser
interrompido a qualquer momento (Ctrl+C, queda de rede, reboot) sem perder
progresso nem gastar consulta repetida, e o export pode rodar quantas vezes
quiser enquanto o worker ainda trabalha.
"""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from .modelo import Dados

ESQUEMA = """
CREATE TABLE IF NOT EXISTS empresas (
    cnpj                     TEXT PRIMARY KEY,
    -- pendente | ok | nao_encontrado | erro | invalido
    status                   TEXT NOT NULL DEFAULT 'pendente',
    tentativas               INTEGER NOT NULL DEFAULT 0,
    fonte                    TEXT,
    erro                     TEXT,
    -- pendente | ok | vazio | erro | dispensado
    status_email             TEXT NOT NULL DEFAULT 'pendente',
    tentativas_email         INTEGER NOT NULL DEFAULT 0,
    fonte_email              TEXT,
    razao_social             TEXT,
    nome_fantasia            TEXT,
    situacao_cadastral       TEXT,
    data_situacao            TEXT,
    telefone_1               TEXT,
    telefone_2               TEXT,
    email                    TEXT,
    cnae_principal_codigo    TEXT,
    cnae_principal_descricao TEXT,
    cnaes_secundarios        TEXT,
    atualizado_em            TEXT
);
CREATE INDEX IF NOT EXISTS idx_status ON empresas(status);
CREATE INDEX IF NOT EXISTS idx_status_email ON empresas(status_email);
"""


def _agora() -> str:
    return datetime.now(timezone.utc).astimezone().isoformat(timespec="seconds")


def _clausula_fila_email(situacoes: list[str]) -> str:
    """Predicado unico da fila da fase 2.

    Usado para montar a fila e para conta-la no resumo. Duplicar essa
    condicao faria o numero relatado divergir do que o worker consulta de
    fato — foi o que aconteceu quando o resumo ignorava a situacao cadastral
    e anunciava 2068 pendentes numa fila real de 1471.

    Parametros na ordem: (*situacoes, max_tentativas).
    """
    marcadores = ",".join("?" for _ in situacoes) or "''"
    return (
        " status = 'ok'"
        " AND status_email IN ('pendente','erro')"
        " AND (email IS NULL OR email = '')"
        f" AND situacao_cadastral IN ({marcadores})"
        " AND tentativas_email < ?"
    )


class Store:
    def __init__(self, caminho: str | Path):
        caminho = Path(caminho)
        caminho.parent.mkdir(parents=True, exist_ok=True)
        self.con = sqlite3.connect(caminho)
        self.con.row_factory = sqlite3.Row
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.executescript(ESQUEMA)
        self.con.commit()

    def fechar(self) -> None:
        self.con.close()

    # -- carga ------------------------------------------------------------

    def inserir_pendentes(self, cnpjs: list[str]) -> int:
        """Adiciona CNPJs novos. Nao mexe nos que ja existem."""
        cur = self.con.executemany(
            "INSERT OR IGNORE INTO empresas (cnpj) VALUES (?)",
            [(c,) for c in cnpjs],
        )
        self.con.commit()
        return cur.rowcount

    def marcar_invalido(self, cnpj: str, motivo: str) -> None:
        self.con.execute(
            "INSERT INTO empresas (cnpj, status, erro, atualizado_em) "
            "VALUES (?, 'invalido', ?, ?) "
            "ON CONFLICT(cnpj) DO UPDATE SET status='invalido', erro=excluded.erro",
            (cnpj, motivo, _agora()),
        )
        self.con.commit()

    # -- filas ------------------------------------------------------------

    def pendentes(self, max_tentativas: int, limite: int = 500) -> list[str]:
        """Fila da fase 1: dados cadastrais."""
        rows = self.con.execute(
            "SELECT cnpj FROM empresas "
            " WHERE status IN ('pendente','erro') AND tentativas < ? "
            " ORDER BY tentativas, cnpj LIMIT ?",
            (max_tentativas, limite),
        ).fetchall()
        return [r["cnpj"] for r in rows]

    def pendentes_email(self, max_tentativas: int, situacoes: list[str],
                        limite: int = 500) -> list[str]:
        """Fila da fase 2: so quem ja tem dados, esta sem e-mail e bate a situacao."""
        rows = self.con.execute(
            f"SELECT cnpj FROM empresas WHERE {_clausula_fila_email(situacoes)}"
            " ORDER BY tentativas_email, cnpj LIMIT ?",
            (*situacoes, max_tentativas, limite),
        ).fetchall()
        return [r["cnpj"] for r in rows]

    # -- escrita ----------------------------------------------------------

    def salvar(self, cnpj: str, dados: Dados, fonte: str) -> None:
        self.con.execute(
            "UPDATE empresas SET status='ok', fonte=?, erro=NULL,"
            " razao_social=?, nome_fantasia=?, situacao_cadastral=?,"
            " data_situacao=?, telefone_1=?, telefone_2=?,"
            " cnae_principal_codigo=?, cnae_principal_descricao=?,"
            " cnaes_secundarios=?, atualizado_em=?,"
            # Se este provedor ja trouxe e-mail, a fase 2 nao precisa rodar.
            " email = CASE WHEN ?<>'' THEN ? ELSE email END,"
            " status_email = CASE WHEN ?<>'' THEN 'ok' ELSE status_email END,"
            " fonte_email = CASE WHEN ?<>'' THEN ? ELSE fonte_email END"
            " WHERE cnpj=?",
            (
                fonte, dados.razao_social, dados.nome_fantasia,
                dados.situacao_cadastral, dados.data_situacao,
                dados.telefone_1, dados.telefone_2,
                dados.cnae_principal_codigo, dados.cnae_principal_descricao,
                json.dumps(dados.cnaes_secundarios, ensure_ascii=False),
                _agora(),
                dados.email, dados.email,
                dados.email,
                dados.email, fonte,
                cnpj,
            ),
        )
        self.con.commit()

    def salvar_email(self, cnpj: str, email: str, fonte: str) -> None:
        self.con.execute(
            "UPDATE empresas SET email=?, fonte_email=?,"
            " status_email=CASE WHEN ?<>'' THEN 'ok' ELSE 'vazio' END,"
            " atualizado_em=? WHERE cnpj=?",
            (email, fonte, email, _agora(), cnpj),
        )
        self.con.commit()

    def registrar_falha(self, cnpj: str, erro: str, definitivo: bool = False,
                        campo: str = "dados",
                        contar_tentativa: bool = True) -> None:
        """Registra falha. `contar_tentativa=False` para estouro de limite,
        que e' condicao passageira e nao deve gastar o orcamento de retentativas.
        """
        passo = 1 if contar_tentativa else 0
        if campo == "email":
            self.con.execute(
                "UPDATE empresas SET tentativas_email = tentativas_email + ?,"
                " status_email = ?, atualizado_em=? WHERE cnpj=?",
                (passo, "vazio" if definitivo else "erro", _agora(), cnpj),
            )
        else:
            self.con.execute(
                "UPDATE empresas SET tentativas = tentativas + ?,"
                " status = ?, erro = ?, atualizado_em=? WHERE cnpj=?",
                (passo, "nao_encontrado" if definitivo else "erro", erro,
                 _agora(), cnpj),
            )
        self.con.commit()

    def reabrir_erros(self) -> int:
        """Zera o contador de tentativas das linhas que pararam em erro.

        Necessario para retomar CNPJ abandonado por falha passageira, cujo
        orcamento de tentativas ja foi consumido.
        """
        cur = self.con.execute(
            "UPDATE empresas SET tentativas = 0 WHERE status = 'erro'"
        )
        n = cur.rowcount
        cur = self.con.execute(
            "UPDATE empresas SET tentativas_email = 0, status_email = 'pendente'"
            " WHERE status_email = 'erro'"
        )
        n += cur.rowcount
        self.con.commit()
        return n

    # -- leitura ----------------------------------------------------------

    def buscar_todos(self) -> dict[str, sqlite3.Row]:
        rows = self.con.execute("SELECT * FROM empresas").fetchall()
        return {r["cnpj"]: r for r in rows}

    def resumo(self, situacoes_email: list[str] | None = None,
               max_tentativas: int = 4) -> dict[str, int]:
        total = self.con.execute("SELECT COUNT(*) c FROM empresas").fetchone()["c"]
        por_status = {
            r["status"]: r["c"]
            for r in self.con.execute(
                "SELECT status, COUNT(*) c FROM empresas GROUP BY status"
            )
        }
        com_tel = self.con.execute(
            "SELECT COUNT(*) c FROM empresas WHERE telefone_1 <> ''"
        ).fetchone()["c"]
        com_email = self.con.execute(
            "SELECT COUNT(*) c FROM empresas WHERE email IS NOT NULL AND email <> ''"
        ).fetchone()["c"]
        com_cnae = self.con.execute(
            "SELECT COUNT(*) c FROM empresas WHERE cnae_principal_codigo <> ''"
        ).fetchone()["c"]
        ativas = self.con.execute(
            "SELECT COUNT(*) c FROM empresas WHERE situacao_cadastral = 'Ativa'"
        ).fetchone()["c"]
        # Conta a fila real da fase 2, com o mesmo predicado que a monta.
        situacoes = situacoes_email or []
        pend_email = self.con.execute(
            f"SELECT COUNT(*) c FROM empresas"
            f" WHERE {_clausula_fila_email(situacoes)}",
            (*situacoes, max_tentativas),
        ).fetchone()["c"]
        sem_email = self.con.execute(
            "SELECT COUNT(*) c FROM empresas WHERE status_email='vazio'"
        ).fetchone()["c"]
        return {
            "email_sem_cadastro": sem_email,
            "total": total,
            # Base dos percentuais: CNPJ invalido na planilha nunca vai ser
            # consultado, entao contá-lo no denominador so mascara a cobertura.
            "consultaveis": total - por_status.get("invalido", 0),
            "com_telefone": com_tel, "com_email": com_email,
            "com_cnae": com_cnae, "ativas": ativas,
            "email_pendente": pend_email, **por_status,
        }
