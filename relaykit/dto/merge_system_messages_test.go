package dto

import (
	"encoding/json"
	"testing"

	"github.com/stretchr/testify/assert"
	"github.com/stretchr/testify/require"
)

func TestMergeMidConversationSystemMessages(t *testing.T) {
	t.Run("folds mid-list system into leading system", func(t *testing.T) {
		req := &GeneralOpenAIRequest{
			Messages: []Message{
				{Role: "system", Content: "You are helpful."},
				{Role: "user", Content: "hi"},
				{Role: "system", Content: "Always answer in Chinese."},
				{Role: "assistant", Content: "你好"},
				{Role: "user", Content: "1+1=?"},
			},
		}
		req.MergeMidConversationSystemMessages()
		require.Len(t, req.Messages, 4)
		assert.Equal(t, "system", req.Messages[0].Role)
		assert.Equal(t, "You are helpful.\n\nAlways answer in Chinese.", req.Messages[0].StringContent())
		assert.Equal(t, "user", req.Messages[1].Role)
		assert.Equal(t, "hi", req.Messages[1].StringContent())
		assert.Equal(t, "assistant", req.Messages[2].Role)
		assert.Equal(t, "user", req.Messages[3].Role)
	})

	t.Run("promotes first mid-list system when none leads", func(t *testing.T) {
		req := &GeneralOpenAIRequest{
			Messages: []Message{
				{Role: "user", Content: "hi"},
				{Role: "system", Content: "Be terse."},
				{Role: "assistant", Content: "ok"},
				{Role: "system", Content: "Use Chinese."},
				{Role: "user", Content: "why?"},
			},
		}
		req.MergeMidConversationSystemMessages()
		require.Len(t, req.Messages, 4)
		assert.Equal(t, "system", req.Messages[0].Role)
		assert.Equal(t, "Be terse.\n\nUse Chinese.", req.Messages[0].StringContent())
		assert.Equal(t, "user", req.Messages[1].Role)
	})

	t.Run("treats developer role as system", func(t *testing.T) {
		req := &GeneralOpenAIRequest{
			Messages: []Message{
				{Role: "developer", Content: "base rules"},
				{Role: "user", Content: "hi"},
				{Role: "developer", Content: "extra rules"},
			},
		}
		req.MergeMidConversationSystemMessages()
		require.Len(t, req.Messages, 2)
		assert.Equal(t, "developer", req.Messages[0].Role)
		assert.Equal(t, "base rules\n\nextra rules", req.Messages[0].StringContent())
	})

	t.Run("folds text parts from media-content system messages", func(t *testing.T) {
		req := &GeneralOpenAIRequest{
			Messages: []Message{
				{Role: "system", Content: "lead"},
				{Role: "user", Content: "hi"},
				{Role: "system", Content: []MediaContent{
					{Type: ContentTypeText, Text: "part one"},
					{Type: ContentTypeImageURL, ImageUrl: &MessageImageUrl{Url: "data:image/png;base64,xx"}},
					{Type: ContentTypeText, Text: "part two"},
				}},
			},
		}
		req.MergeMidConversationSystemMessages()
		require.Len(t, req.Messages, 2)
		assert.Equal(t, "lead\n\npart one\n\npart two", req.Messages[0].StringContent())
	})

	t.Run("no mid-list system is a no-op", func(t *testing.T) {
		original := []Message{
			{Role: "system", Content: "s"},
			{Role: "user", Content: "u"},
			{Role: "assistant", Content: "a"},
		}
		req := &GeneralOpenAIRequest{Messages: append([]Message(nil), original...)}
		req.MergeMidConversationSystemMessages()
		assert.Equal(t, original, req.Messages)
	})

	t.Run("all-system message list is a no-op", func(t *testing.T) {
		req := &GeneralOpenAIRequest{
			Messages: []Message{
				{Role: "system", Content: "a"},
				{Role: "system", Content: "b"},
			},
		}
		req.MergeMidConversationSystemMessages()
		require.Len(t, req.Messages, 2)
		assert.Equal(t, "a", req.Messages[0].StringContent())
	})

	t.Run("keeps tool messages and metadata intact", func(t *testing.T) {
		toolCall := json.RawMessage(`[{"id":"call_1","type":"function","function":{"name":"f","arguments":"{}"}}]`)
		req := &GeneralOpenAIRequest{
			Messages: []Message{
				{Role: "system", Content: "s"},
				{Role: "user", Content: "u"},
				{Role: "system", Content: "reminder"},
				{Role: "assistant", Content: "", ToolCalls: toolCall},
				{Role: "tool", Content: "result", ToolCallId: "call_1"},
			},
		}
		req.MergeMidConversationSystemMessages()
		require.Len(t, req.Messages, 4)
		assert.Equal(t, "system", req.Messages[0].Role)
		assert.Equal(t, "s\n\nreminder", req.Messages[0].StringContent())
		assert.Equal(t, "assistant", req.Messages[2].Role)
		assert.Equal(t, "tool", req.Messages[3].Role)
		assert.Equal(t, "call_1", req.Messages[3].ToolCallId)
	})
}
