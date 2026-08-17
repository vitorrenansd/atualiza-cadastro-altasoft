"""Testes dos parsers e da normalizacao.

Os payloads sao recortes reais das respostas de cada API (Petrobras,
33.000.167/0001-01). Se algum provedor mudar o formato, o teste quebra aqui
em vez de o worker gravar 2300 linhas vazias sem ninguem perceber.

Rodar: python -m unittest discover -s tests -v
"""

import unittest

from cnpj_updater import cnpj
from cnpj_updater.providers import BrasilAPI, CNPJa, CNPJws, ReceitaWS
from cnpj_updater.providers.base import ErroProvedor, LimiteExcedido
from cnpj_updater.ratelimit import Limitador


class TestCNPJ(unittest.TestCase):
    def test_normaliza_formatos(self):
        for entrada in ("33.000.167/0001-01", "33000167000101", 33000167000101,
                        " 33.000.167/0001-01 "):
            self.assertEqual(cnpj.normalizar(entrada), "33000167000101")

    def test_recupera_zero_a_esquerda(self):
        # Excel guarda CNPJ como numero e come o zero: 191 e o Banco do Brasil.
        self.assertEqual(cnpj.normalizar(191), "00000000000191")
        self.assertTrue(cnpj.valido("00000000000191"))

    def test_float_do_excel(self):
        self.assertEqual(cnpj.normalizar(33000167000101.0), "33000167000101")

    def test_preenchimento_de_15_posicoes(self):
        # A base de origem grava o campo com 15 posicoes, preenchido com zero
        # a esquerda: 14% da planilha real vem assim. Rejeitar por tamanho
        # descartaria centenas de clientes validos sem aviso.
        self.assertEqual(cnpj.normalizar("'082509423000104"), "82509423000104")
        self.assertTrue(cnpj.valido("82509423000104"))

    def test_apostrofo_de_texto_do_excel(self):
        self.assertEqual(cnpj.normalizar("'05879113000122"), "05879113000122")
        self.assertIsNone(cnpj.normalizar("'"))

    def test_rejeita_lixo(self):
        self.assertIsNone(cnpj.normalizar(None))
        self.assertIsNone(cnpj.normalizar("sem digitos"))
        # Excedente com digito significativo e erro de cadastro, nao
        # preenchimento: nao da para adivinhar o que remover.
        self.assertIsNone(cnpj.normalizar("123456789012345"))

    def test_digito_verificador(self):
        self.assertTrue(cnpj.valido("33000167000101"))
        self.assertFalse(cnpj.valido("33000167000102"))
        self.assertFalse(cnpj.valido("11111111111111"))

    def test_formata_cnae(self):
        self.assertEqual(cnpj.formatar_cnae("0600001"), "0600-0/01")
        self.assertEqual(cnpj.formatar_cnae(600001), "0600-0/01")
        self.assertEqual(cnpj.formatar_cnae(None), "")


PAYLOAD_DUMP = {
    "razao_social": "PETROLEO BRASILEIRO S A PETROBRAS",
    "nome_fantasia": "PETROBRAS - EDISE",
    "descricao_situacao_cadastral": "ATIVA",
    "data_situacao_cadastral": "2005-11-03",
    "ddd_telefone_1": "2121660000",
    "ddd_telefone_2": "",
    "email": None,
    "cnae_fiscal": 600001,
    "cnae_fiscal_descricao": "Extração de petróleo e gás natural",
    "cnaes_secundarios": [
        {"codigo": 1921700, "descricao": "Fabricação de produtos do refino"},
        {"codigo": 0, "descricao": "Nao informada"},
    ],
}


class TestBrasilAPI(unittest.TestCase):
    def setUp(self):
        self.d = BrasilAPI(None).analisar(PAYLOAD_DUMP)

    def test_campos_principais(self):
        self.assertEqual(self.d.situacao_cadastral, "Ativa")
        self.assertEqual(self.d.cnae_principal_codigo, "0600-0/01")
        self.assertEqual(self.d.telefone_1, "(21) 2166-0000")

    def test_descarta_cnae_zero(self):
        # codigo 0 e marcador de "sem secundario", nao um CNAE real.
        self.assertEqual(len(self.d.cnaes_secundarios), 1)
        self.assertEqual(self.d.cnaes_secundarios[0][0], "1921-7/00")

    def test_email_sempre_vazio(self):
        self.assertEqual(self.d.email, "")
        self.assertFalse(BrasilAPI.fornece_email)


class TestCNPJa(unittest.TestCase):
    def setUp(self):
        self.d = CNPJa(None).analisar({
            "alias": "Petrobras - Edise",
            "company": {"name": "PETROLEO BRASILEIRO S A PETROBRAS"},
            "status": {"id": 2, "text": "Ativa"},
            "statusDate": "2005-11-03",
            "phones": [{"area": "21", "number": "21660000"}],
            "emails": [{"address": "cc-rfisc@petrobras.com.br"}],
            "mainActivity": {"id": 600001, "text": "Extração de petróleo"},
            "sideActivities": [{"id": 1921700, "text": "Refino"}],
        })

    def test_lista_para_campo_unico(self):
        self.assertEqual(self.d.telefone_1, "(21) 2166-0000")
        self.assertEqual(self.d.telefone_2, "")
        self.assertEqual(self.d.email, "cc-rfisc@petrobras.com.br")

    def test_cnaes(self):
        self.assertEqual(self.d.cnae_principal_codigo, "0600-0/01")
        self.assertEqual(self.d.cnaes_secundarios, [("1921-7/00", "Refino")])


class TestCNPJws(unittest.TestCase):
    def test_aninhado_em_estabelecimento(self):
        d = CNPJws(None).analisar({
            "razao_social": "PETROLEO BRASILEIRO S A PETROBRAS",
            "estabelecimento": {
                "nome_fantasia": "PETROBRAS",
                "situacao_cadastral": "Ativa",
                "ddd1": "21", "telefone1": "21660000",
                "ddd2": None, "telefone2": None,
                "email": "cc-rfisc@petrobras.com.br",
                "atividade_principal": {"id": "0600001",
                                        "descricao": "Extração de petróleo"},
                "atividades_secundarias": [{"id": "1921700",
                                            "descricao": "Refino"}],
            },
        })
        self.assertEqual(d.situacao_cadastral, "Ativa")
        self.assertEqual(d.telefone_1, "(21) 2166-0000")
        self.assertEqual(d.telefone_2, "")
        self.assertEqual(d.cnae_principal_codigo, "0600-0/01")


class TestReceitaWS(unittest.TestCase):
    def test_sucesso(self):
        d = ReceitaWS(None).analisar({
            "status": "OK",
            "nome": "PETROLEO BRASILEIRO S A PETROBRAS",
            "fantasia": "PETROBRAS - EDISE",
            "situacao": "ATIVA",
            "telefone": "(21) 2166-0000",
            "email": "cc-rfisc@petrobras.com.br",
            "atividade_principal": [{"code": "06.00-0-01",
                                     "text": "Extração de petróleo"}],
            "atividades_secundarias": [{"code": "00.00-0-00",
                                        "text": "Não informada"}],
        })
        self.assertEqual(d.situacao_cadastral, "Ativa")
        self.assertEqual(d.cnae_principal_codigo, "0600-0/01")
        self.assertEqual(d.cnaes_secundarios, [])  # 00.00 e marcador

    def test_erro_com_http_200(self):
        # Esta API sinaliza falha no corpo, com status 200. Sem tratar isso o
        # worker gravaria "sucesso" com todos os campos em branco.
        with self.assertRaises(ErroProvedor):
            ReceitaWS(None).analisar({"status": "ERROR",
                                      "message": "CNPJ inválido"})

    def test_limite_no_corpo(self):
        with self.assertRaises(LimiteExcedido):
            ReceitaWS(None).analisar({
                "status": "ERROR",
                "message": "Você excedeu o número de consultas permitidas",
            })


class TestMontarValores(unittest.TestCase):
    """O bloco de colunas muda de tamanho conforme comparar_contato."""

    def _layout(self, comparar):
        from cnpj_updater.config import Config
        from cnpj_updater.excel_io import colunas
        cfg = Config(comparar_contato=comparar)
        return colunas(cfg)

    def test_status_pela_chave_nao_pela_posicao(self):
        from cnpj_updater.excel_io import Linha, _montar_valores
        invalida = Linha(numero=9, bruto="'11111111111111",
                         cnpj="11111111111111", valido=False)
        for comparar in (False, True):
            layout = self._layout(comparar)
            valores = _montar_valores(invalida, None, layout)
            chaves = [c for _, c in layout]
            self.assertEqual(len(valores), len(layout))
            self.assertEqual(valores[chaves.index("status")],
                             "CNPJ invalido na planilha")
            # Nenhuma outra coluna recebe a mensagem.
            self.assertEqual(sum(1 for v in valores if v), 1)

    def test_comparacao_muda_o_tamanho_do_bloco(self):
        self.assertEqual(len(self._layout(True)) - len(self._layout(False)), 2)


class TestComparacaoContato(unittest.TestCase):
    def test_email(self):
        from cnpj_updater.excel_io import _veredito_email as v
        self.assertEqual(v("A@X.COM", "a@x.com"), "igual")
        self.assertEqual(v("a@x.com", "b@x.com"), "divergente")
        self.assertEqual(v("", "b@x.com"), "só na Receita")
        self.assertEqual(v("a@x.com", ""), "só na planilha")
        self.assertEqual(v("", ""), "ambos vazios")

    def test_email_nao_consultado_nao_vira_conclusao(self):
        from cnpj_updater.excel_io import _veredito_email_com_status as v
        # Linha resolvida por BrasilAPI/MinhaReceita: e-mail nunca perguntado.
        # Dizer "so na planilha" afirmaria que a Receita nao tem.
        self.assertEqual(v("a@x.com", "", "pendente"), "não consultado")
        self.assertEqual(v("", "", "pendente"), "não consultado")
        # Depois da fase 2, o veredito passa a valer.
        self.assertEqual(v("a@x.com", "", "vazio"), "só na planilha")
        self.assertEqual(v("a@x.com", "a@x.com", "ok"), "igual")
        self.assertEqual(v("a@x.com", "b@x.com", "ok"), "divergente")

    def test_telefone_ignora_ddd_e_nono_digito(self):
        from cnpj_updater.excel_io import _veredito_telefone as v
        # Mesmo numero, formatos diferentes: nao pode marcar divergente.
        self.assertEqual(v(["'4830351244"], "(48) 3035-1244", ""), "igual")
        self.assertEqual(v(["30351244"], "(48) 3035-1244", ""), "igual")
        self.assertEqual(v(["'48998199912"], "(48) 99819-9912", ""), "igual")
        self.assertEqual(v(["'4830351244"], "(48) 9999-0000", ""), "divergente")

    def test_telefone_confere_contra_qualquer_coluna(self):
        from cnpj_updater.excel_io import _veredito_telefone as v
        # A base tem Comercial/Celular/Residencial/Outros; bater com uma basta.
        self.assertEqual(
            v(["'", "'48998199912", "'", "'"], "(48) 99819-9912", ""), "igual"
        )

    def test_telefone_vazios(self):
        from cnpj_updater.excel_io import _veredito_telefone as v
        # Apostrofo sozinho e' celula "vazia marcada como texto" na base real.
        self.assertEqual(v(["'", "'"], "", ""), "ambos vazios")
        self.assertEqual(v(["'"], "(48) 3035-1244", ""), "só na Receita")
        self.assertEqual(v(["'4830351244"], "", ""), "só na planilha")


class TestLimitador(unittest.TestCase):
    def test_balde_comeca_cheio(self):
        lim = Limitador("x", rpm=3)
        for _ in range(3):
            self.assertTrue(lim.disponivel())
            lim.consumir()
        self.assertFalse(lim.disponivel())
        self.assertGreater(lim.espera(), 0)

    def test_cooldown_escala_e_zera(self):
        lim = Limitador("x", rpm=3, cooldown_base=10)
        self.assertEqual(lim.penalizar(), 10)
        self.assertEqual(lim.penalizar(), 20)
        self.assertEqual(lim.penalizar(), 40)
        lim.premiar()
        self.assertEqual(lim.penalizar(), 10)

    def test_respeita_retry_after(self):
        lim = Limitador("x", rpm=3)
        self.assertEqual(lim.penalizar(retry_after=7), 7)
        self.assertFalse(lim.disponivel())


if __name__ == "__main__":
    unittest.main()
