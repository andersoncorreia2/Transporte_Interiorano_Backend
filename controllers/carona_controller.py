from flask import jsonify, request
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

    # 🟢 ADICIONE ESTA NOVA ROTA AQUI EMBAIXO:
    @app.route("/caronas/<int:id_carona>", methods=["PUT"])
    def atualizar_carona(id_carona):
        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        cursor = conexao.cursor()
        try:
            cursor.execute("""
                UPDATE caronas 
                SET evento_nome = %s, cidade_origem = %s, endereco_origem = %s, 
                    cidade_destino = %s, endereco_destino = %s, horario = %s, vagas = %s
                WHERE id = %s
            """, (
                dados.get("evento_nome"),
                dados.get("cidade_origem"),
                dados.get("endereco_origem"),
                dados.get("cidade_destino"),
                dados.get("endereco_destino"),
                dados.get("horario"),
                dados.get("vagas"),
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