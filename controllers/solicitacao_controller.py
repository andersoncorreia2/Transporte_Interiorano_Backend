import urllib.parse
from flask import jsonify, request
# 🔌 Puxando todas as funções corretas da caixinha de Models, incluindo a nova!
from models.solicitacao_model import (
    model_listar_e_expirar_solicitacoes,
    model_pedir_carona_fluxo,
    model_cancelar_solicitacao_simples,
    model_finalizar_solicitacao_fluxo,
    model_listar_historico_passageiro,
    model_listar_historico_motorista,
    model_cancelar_carona_geral_fluxo,
    model_executar_cancelamento_banco,
    model_atualizar_status_solicitacao
)

def configurar_rotas_solicitacao(app, conectar_banco, enviar_notificacao):

    @app.route("/solicitacoes", methods=["GET"])
    def listar_solicitacoes():
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
        
        try:
            solicitacoes_do_cofre = model_listar_e_expirar_solicitacoes(conexao)
            lista_final = []
            for sol in solicitacoes_do_cofre:
                lista_final.append({
                    "id": sol["id"], "carona_id": sol["carona_id"], "passageiro": sol["passageiro"],
                    "status": sol["status"], "passageiro_cpf": sol.get("passageiro_cpf", "")
                })
            return jsonify(lista_final), 200
        except Exception as e:
            print(f"❌ Erro no relógio da Viagem Programada: {e}")
            return jsonify({"erro": str(e)}), 500

    @app.route("/solicitacoes", methods=["POST"])
    def pedir_carona():
        dados = request.get_json()
        carona_id = int(dados["carona_id"])
        cpf_passageiro = dados.get("passageiro_cpf")
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
        
        try:
            tokens = model_pedir_carona_fluxo(conexao, carona_id, cpf_passageiro, dados)
            if tokens is None:
                return jsonify({"erro": "Carona inexistente."}), 400

            if tokens["motorista_token"]:
                enviar_notificacao(tokens["motorista_token"], "Nova Solicitação!", f"{dados['passageiro']} quer uma vaga.")

            if tokens["passageiro_token"]:
                enviar_notificacao(tokens["passageiro_token"], "⏳ Reserva Iniciada!", "Você tem 15 minutos para confirmar o pagamento.")
                
            return jsonify({"mensagem": "Pedido registrado com sucesso!"}), 201
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/solicitacoes/<int:id_solicitacao>", methods=["DELETE"])
    def cancelar_solicitacao(id_solicitacao):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            sucesso = model_cancelar_solicitacao_simples(conexao, id_solicitacao)
            if sucesso:
                return jsonify({"mensagem": "Pedido cancelado com sucesso!"}), 200
            return jsonify({"erro": "Solicitação não encontrada."}), 404
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/finalizar_solicitacao", methods=["POST"])
    def finalizar_solicitacao():
        dados = request.get_json()
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            model_finalizar_solicitacao_fluxo(conexao, dados["solicitacao_id"])
            return jsonify({"mensagem": "Viagem finalizada com sucesso!"}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/historico_cpf/<cpf>", methods=["GET"])
    def listar_historico_passageiro_por_cpf(cpf):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            historico = model_listar_historico_passageiro(conexao, cpf)
            return jsonify(historico), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/historico_motorista_cpf/<cpf>", methods=["GET"])
    def listar_historico_motorista_por_cpf(cpf):
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            historico = model_listar_historico_motorista(conexao, cpf)
            return jsonify(historico), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    @app.route("/cancelar_carona_geral", methods=["POST"])
    def cancelar_carona_geral():
        dados = request.get_json()
        carona_id = dados.get("carona_id")
        motivo_cancelamento = dados.get("motivo", "Motivo de força maior")
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            info_cancelamento = model_cancelar_carona_geral_fluxo(conexao, carona_id)
            
            # Envia as notificações para os passageiros afetados
            for pass_info in info_cancelamento["passageiros"]:
                token = pass_info.get("fcm_token")
                if token:
                    enviar_notificacao(token, f"⚠️ Viagem Cancelada: {info_cancelamento['nome_evento']}", f"O motorista precisou cancelar. Motivo: {motivo_cancelamento}.")

            # Executa a exclusão e atualização lógica de tabelas
            model_executar_cancelamento_banco(conexao, carona_id, motivo_cancelamento)
            return jsonify({"mensagem": "Viagem derrubada com sucesso!"}), 200
        except Exception as e:
            return jsonify({"erro": str(e)}), 500

    # 🟢 AGORA SIM! O PUT ESTÁ INDENTADO CORRETAMENTE DENTRO DA FUNÇÃO CONFIGURAR_ROTAS
    @app.route("/solicitacoes/<int:id_solicitacao>", methods=["PUT"])
    def responder_solicitacao(id_solicitacao):
        dados = request.get_json()
        novo_status = dados.get("status")
        
        conexao = conectar_banco()
        if not conexao:
            return jsonify({"erro": "Falha na conexão com o banco"}), 500
            
        try:
            info_notificacao = model_atualizar_status_solicitacao(conexao, id_solicitacao, novo_status)
            
            if info_notificacao and info_notificacao["passageiro_token"]:
                titulo = "🎉 Carona Confirmada!" if novo_status == "Aceito" else "❌ Solicitação Recusada"
                corpo = f"O motorista aceitou seu pedido para {info_notificacao['evento_nome']}." if novo_status == "Aceito" else f"Seu pedido para {info_notificacao['evento_nome']} foi recusado."
                enviar_notificacao(info_notificacao["passageiro_token"], titulo, corpo)
                
            return jsonify({"mensagem": f"Solicitação updated para {novo_status} com sucesso!"}), 200
        except Exception as e:
            print(f"❌ Erro ao responder solicitação via PUT: {e}")
            return jsonify({"erro": str(e)}), 500