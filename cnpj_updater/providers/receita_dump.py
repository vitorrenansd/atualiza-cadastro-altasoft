"""BrasilAPI e Minha Receita.

Os dois servem o mesmo dump da Receita Federal com o mesmo esquema de
campos, entao compartilham o parser. Ambos retornam `email` sempre nulo
(redigido na origem), por isso `fornece_email = False`: usa-los na fase 2
seria gastar requisicao a troco de nada.
"""

from ..cnpj import formatar_cnae
from ..modelo import Dados, normalizar_situacao, separar_telefone
from .base import Provedor


class _DumpReceita(Provedor):
    fornece_email = False

    def analisar(self, payload: dict) -> Dados:
        secundarios = []
        for item in payload.get("cnaes_secundarios") or []:
            codigo = item.get("codigo")
            # O dump usa 0 como marcador de "nenhum CNAE secundario".
            if not codigo:
                continue
            secundarios.append((formatar_cnae(codigo), item.get("descricao") or ""))

        principal = payload.get("cnae_fiscal")
        return Dados(
            razao_social=payload.get("razao_social") or "",
            nome_fantasia=payload.get("nome_fantasia") or "",
            situacao_cadastral=normalizar_situacao(
                payload.get("descricao_situacao_cadastral")
            ),
            data_situacao=payload.get("data_situacao_cadastral") or "",
            telefone_1=separar_telefone(payload.get("ddd_telefone_1")),
            telefone_2=separar_telefone(payload.get("ddd_telefone_2")),
            email="",  # sempre nulo nestas duas fontes
            cnae_principal_codigo=formatar_cnae(principal),
            cnae_principal_descricao=payload.get("cnae_fiscal_descricao") or "",
            cnaes_secundarios=secundarios,
        )


class BrasilAPI(_DumpReceita):
    nome = "brasilapi"

    def url(self, cnpj: str) -> str:
        return f"https://brasilapi.com.br/api/cnpj/v1/{cnpj}"


class MinhaReceita(_DumpReceita):
    nome = "minhareceita"

    def url(self, cnpj: str) -> str:
        return f"https://minhareceita.org/{cnpj}"
