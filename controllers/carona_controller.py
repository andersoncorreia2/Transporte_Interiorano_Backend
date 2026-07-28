from flask import jsonify, request
from datetime import datetime, timezone  # 🟢 ADICIONADO PARA LIDAR COM DATAS
from psycopg2.extras import RealDictCursor  # 🟢 ADICIONADO PARA O CURSOR DICIONÁRIO
# 🔌 Puxando os fios da nossa nova caixinha de Models!
from models.carona_model import model_listar_caronas, model_criar_carona, model_deletar_carona

def configurar_rotas_carona(app, conectar_banco):

    @app.route("/caronas/<cpf_passageiro>", methods=["GET"])
    def listar_caronas(cpf_passageiro):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
        
        try:
            caronas_limpas = model_listar_caronas(conexao, cpf_passageiro)
            return jsonify(caronas_limpas), 200
        except Exception as e:
            print(f"❌ Erro ao listar caronas filtradas: {e}")
            return jsonify({"erro": str(e)}), 500

    @app.route("/caronas", methods=["POST"])
    def criar_carona():
        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
        
        try:
            model_criar_carona(conexao, dados)
            return jsonify({"mensagem": "Evento criado!"}), 201
        except Exception as e:
            print(f"❌ Erro ao criar carona: {e}")
            return jsonify({"erro": str(e)}), 500

    @app.route("/caronas/<int:id_carona>", methods=["DELETE"])
    def deletar_carona(id_carona):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            model_deletar_carona(conexao, id_carona)
            return jsonify({"mensagem": "Evento e solicitações excluídos com sucesso!"}), 200
        except Exception as e:
            print(f"❌ Erro ao deletar carona: {e}")
            return jsonify({"erro": str(e)}), 500

    @app.route("/caronas/<int:id_carona>", methods=["PUT"])
    def atualizar_carona(id_carona):
        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        cursor = conexao.cursor()
        try:
            # 🟢 CORREÇÃO: Incluído o 'valor_corrida = %s' na query de atualização
            cursor.execute("""
                UPDATE caronas 
                SET evento_nome = %s, cidade_origem = %s, endereco_origem = %s, 
                    cidade_destino = %s, endereco_destino = %s, horario = %s, vagas = %s, valor_corrida = %s
                WHERE id = %s
            """, (
                dados.get("evento_nome"),
                dados.get("cidade_origem"),
                dados.get("endereco_origem"),
                dados.get("cidade_destino"),
                dados.get("endereco_destino"),
                dados.get("horario"),
                dados.get("vagas"),
                dados.get("valor_corrida", 0.00), # 🟢 Captura o valor enviado pelo Android
                id_carona
            ))
            conexao.commit()
            return jsonify({"mensagem": "Carona atualizada com sucesso!"}), 200
        except Exception as e:
            print(f"❌ Erro ao atualizar carona no banco: {e}")
            conexao.rollback()
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()
            
    # 🟢 ROTA PARA VALIDAR AS REGRAS DE PRAZO (72h e 24h) DA VIAGEM PROGRAMADA
    @app.route("/caronas/verificar_prazo_reserva/<int:id_carona>", methods=["GET"])
    def verificar_prazo_reserva(id_carona):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Banco offline"}), 500
        
        cursor = conexao.cursor(cursor_factory=RealDictCursor)
        try:
            cursor.execute("SELECT horario, valor_corrida FROM caronas WHERE id = %s", (id_carona,))
            carona = cursor.fetchone()
            
            if not carona:
                return jsonify({"erro": "Carona não encontrada"}), 404
                
            horario_str = carona["horario"] # Ex: "28/07/2026 às 14:00"
            valor_total = float(carona["valor_corrida"] or 0.0)
            
            # Converte a string do horário da carona para um objeto datetime real do Python
            # Ajuste o formato caso o seu app salve de forma diferente
            try:
                # Exemplo de formato: "DD/MM/YYYY às HH:MM"
                partes = horario_str.split(" às ")
                data_partes = partes[0].split("/")
                hora_partes = partes[1].split(":")
                
                data_viagem = datetime(
                    year=int(data_partes[2]),
                    month=int(data_partes[1]),
                    day=int(data_partes[0]),
                    hour=int(hora_partes[0]),
                    minute=int(hora_partes[1]),
                    tzinfo=timezone.utc
                )
            except Exception:
                # Fallback seguro caso o formato venha diferente
                return jsonify({"permite_taxa_reserva": True, "valor_total": valor_total}), 200

            agora = datetime.now(timezone.utc)
            diferenca_horas = (data_viagem - agora).total_seconds() / 3600
            
            # Se faltam MAIS de 72 horas, permite a taxa de reserva de R$ 5,00 (Regra das 24h)
            # Se faltam MENOS de 72 horas, NÃO permite a taxa, apenas pagamento total em 15 min.
            permite_taxa_reserva = diferenca_horas > 72
            
            return jsonify({
                "permite_taxa_reserva": permite_taxa_reserva,
                "valor_total": valor_total,
                "horas_faltando": round(diferenca_horas, 1)
            }), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500
        finally:
            cursor.close()
            conexao.close()